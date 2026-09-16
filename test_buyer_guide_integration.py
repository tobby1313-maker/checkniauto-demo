"""V2 publication via the unchanged local queue and OAuth/MCP transports."""
import base64
import hashlib
import html
import io
import json
import re
import tempfile
import unittest
from urllib.parse import urlencode, urlsplit, parse_qs
from unittest.mock import patch
import zipfile

from beta_server import create_beta_app
from scrapper_demo.beta_contracts import report_template
from scrapper_demo.local_beta_mcp import REPORT_SCHEMA, CHATGPT_PUBLIC_CLIENT_ID, CHATGPT_PUBLIC_REDIRECT
from test_local_beta import prepared
from test_buyer_guide import full_report

ORIGIN = "https://checkni.example"
ADMIN = "buyer-guide-synthetic-only-not-a-real-secret"


class BuyerGuideIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app = create_beta_app({"TESTING":True,"CHECKNI_STORAGE_MODE":"local",
            "CHECKNI_LOCAL_DATA_DIR":self.tmp.name,"CHECKNI_PUBLIC_ORIGIN":ORIGIN,
            "CHECKNI_OPERATOR_TOKEN":ADMIN,"CHECKNI_MCP_READ_ONLY":False,"CHECKNI_BETA_MARKET_SEARCH":False})
        self.client = self.app.test_client();self.hub = self.app.extensions["beta_hub"]
        self.job, self.manifest = prepared(self.hub,photos=True)
        self.report = full_report(self.job,self.manifest)
        self.headers = {"Authorization":"Bearer "+ADMIN}

    def op(self, action, body):
        return self.client.post(f"/_beta/operator/jobs/{self.job}/{action}",headers=self.headers,json=body)

    def rpc(self, method, params=None, token=None):
        return self.client.post("/mcp",base_url=ORIGIN,json={"jsonrpc":"2.0","id":1,"method":method,"params":params or {}},
                                headers={"Authorization":"Bearer "+token} if token else {})

    def login(self):
        verifier = "v"*64
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        query={"client_id":CHATGPT_PUBLIC_CLIENT_ID,"redirect_uri":CHATGPT_PUBLIC_REDIRECT,"response_type":"code",
               "code_challenge_method":"S256","code_challenge":challenge,"resource":ORIGIN+"/mcp",
               "scope":"analysis:read analysis:write offline_access","state":"synthetic"}
        page=self.client.get("/oauth/authorize?"+urlencode(query),base_url=ORIGIN)
        self.assertEqual(page.status_code,200)
        signed=html.unescape(re.search('name="signed" value="([^"]+)"',page.text).group(1))
        response=self.client.post("/oauth/authorize",base_url=ORIGIN,headers={"Origin":ORIGIN},data={"signed":signed,"secret":ADMIN})
        self.assertEqual(response.status_code,303)
        code=parse_qs(urlsplit(response.location).query)["code"][0]
        tokens=self.client.post("/oauth/token",base_url=ORIGIN,data={"grant_type":"authorization_code","code":code,"code_verifier":verifier,
             "client_id":CHATGPT_PUBLIC_CLIENT_ID,"redirect_uri":CHATGPT_PUBLIC_REDIRECT,"resource":ORIGIN+"/mcp"})
        self.assertEqual(tokens.status_code,200)
        return tokens.json["access_token"]

    def test_new_template_and_instructions_are_in_live_get_tool(self):
        token=self.login()
        r=self.rpc("tools/call",{"name":"checkniauto_get_analysis","arguments":{"job_id":self.job}},token)
        data=json.loads(r.json["result"]["content"][0]["text"])
        self.assertEqual(data["report_template"]["schema_version"],2)
        self.assertIn("owners",data["report_template"]["buyer_guide"])
        self.assertIn("BUYER GUIDE V2 IS REQUIRED",data["review_instructions"])
        self.assertIn("raw_listing",data)

    def test_new_mcp_schema_is_versioned_and_oauth_scope_unchanged(self):
        tools=self.rpc("tools/list").json["result"]["tools"]
        self.assertEqual(len(tools),7)
        complete=next(t for t in tools if t["name"]=="checkniauto_complete_analysis")
        schema=complete["inputSchema"]["properties"]["report"]
        self.assertEqual(schema["properties"]["schema_version"]["enum"],[2])
        self.assertIn("buyer_guide",schema["required"])
        self.assertIn("source_type",schema["properties"]["sources"]["items"]["required"])
        self.assertEqual(complete["securitySchemes"][0]["scopes"],["analysis:write"])
        self.assertEqual(self.rpc("initialize").json["result"]["serverInfo"]["version"],"1.3.0")

    def test_actual_mcp_claim_and_publish_full_guide_without_api(self):
        token=self.login()
        with patch("requests.request",side_effect=AssertionError("No model/cloud request")):
            claim=self.rpc("tools/call",{"name":"checkniauto_claim_analysis","arguments":{"job_id":self.job}},token)
            lease=json.loads(claim.json["result"]["content"][0]["text"])["lease_token"]
            args={"job_id":self.job,"lease_token":lease,"report":self.report}
            response=self.rpc("tools/call",{"name":"checkniauto_complete_analysis","arguments":args},token)
            self.assertNotIn("isError",response.json["result"])
            result=json.loads(response.json["result"]["content"][0]["text"])
            self.assertEqual(result["status"],"DONE")
            self.assertEqual(result["research_coverage"]["status"],"COMPLETE")
        saved=self.client.get("/_beta/jobs/"+self.job).json
        self.assertEqual(saved["report"],self.report)
        self.assertEqual(len(saved["photos"]),2)
        self.assertFalse(saved["chargeable"]);self.assertEqual(saved["ai_api_calls"],0)

    def test_invalid_guide_cannot_replace_report_or_change_source_assets(self):
        before=self.hub.read_asset(self.job,"raw_data.json")
        lease=self.op("claim",{}).json["lease_token"]
        del self.report["buyer_guide"]["engine"]
        response=self.op("complete",{"lease_token":lease,"report":self.report})
        self.assertEqual(response.status_code,400)
        self.assertIsNone(self.hub.get_job(self.job)["report"])
        self.assertEqual(self.hub.get_job(self.job)["status"],"PROCESSING")
        self.assertEqual(self.hub.read_asset(self.job,"raw_data.json"),before)

    def test_legacy_json_import_is_retained_and_not_upgraded_as_factual_research(self):
        r=report_template(self.job,self.manifest,version=1)
        r["summary"]="Legacy report, no model-specific research claimed."
        lease=self.op("claim",{}).json["lease_token"]
        self.assertEqual(self.op("complete",{"lease_token":lease,"report":r}).status_code,200)
        saved=self.client.get("/_beta/jobs/"+self.job).json["report"]
        self.assertEqual(saved["schema_version"],1);self.assertNotIn("buyer_guide",saved)

    def test_zip_export_contains_v2_template_and_research_instructions(self):
        response=self.client.get(f"/_beta/operator/jobs/{self.job}/bundle",headers=self.headers)
        self.assertEqual(response.status_code,200)
        with zipfile.ZipFile(io.BytesIO(response.data)) as z:
            template=json.loads(z.read("report-template.json"))
            self.assertEqual(template["schema_version"],2)
            self.assertIn("model_risks",template["buyer_guide"])
            self.assertIn("source_type",z.read("INSTRUCTIONS.md").decode())
            self.assertIn("images/p001.jpg",z.namelist())

    def test_missing_write_auth_stays_blocked(self):
        response=self.rpc("tools/call",{"name":"checkniauto_complete_analysis","arguments":{
            "job_id":self.job,"lease_token":"not-authorized","report":self.report}})
        self.assertEqual(response.status_code,401)
        self.assertEqual(self.hub.get_job(self.job)["status"],"WAITING_FOR_AI")


if __name__=="__main__":unittest.main()
