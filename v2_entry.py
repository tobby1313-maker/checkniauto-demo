from __future__ import annotations

import os
import re
from pathlib import Path

from flask import Response, jsonify, send_from_directory

import v2_app

app = v2_app.app
PHOTO_ID_RE = re.compile(r"^photo-\d{3}$")


@app.route(
    "/api/v2/jobs/<job_id>/photos/<photo_id>",
    methods=["GET"],
    endpoint="v2_job_photo",
)
def job_photo(job_id: str, photo_id: str) -> Response:
    """Serve one optimized listing photo only when it belongs to a completed report."""
    if not PHOTO_ID_RE.fullmatch(photo_id):
        return jsonify({"error": "Fotografia nebola nájdená.", "code": "not_found"}), 404

    job = v2_app._read_job(job_id)
    if job is None or job.get("status") != "done":
        return jsonify({"error": "Fotografia nebola nájdená.", "code": "not_found"}), 404

    report = job.get("report") if isinstance(job.get("report"), dict) else {}
    photo_analysis = (
        report.get("photo_analysis")
        if isinstance(report.get("photo_analysis"), dict)
        else {}
    )
    gallery = photo_analysis.get("gallery")
    allowed_ids = (
        {
            str(item.get("id") or "")
            for item in gallery
            if isinstance(item, dict)
        }
        if isinstance(gallery, list)
        else set()
    )
    if photo_id not in allowed_ids:
        return jsonify({"error": "Fotografia nebola nájdená.", "code": "not_found"}), 404

    try:
        directory = v2_app._job_dir(job_id) / "gallery"
    except ValueError:
        return jsonify({"error": "Fotografia nebola nájdená.", "code": "not_found"}), 404
    filename = f"{photo_id}.jpg"
    path = directory / filename
    if not path.is_file():
        return jsonify({"error": "Fotografia nebola nájdená.", "code": "not_found"}), 404

    response = send_from_directory(
        Path(directory),
        filename,
        mimetype="image/jpeg",
        conditional=True,
        max_age=3600,
    )
    response.headers["Cache-Control"] = "private, max-age=3600"
    response.headers["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


__all__ = ["app"]


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=False,
        threaded=True,
    )
