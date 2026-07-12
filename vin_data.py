"""
VIN Data — WMI Manufacturer Map, Model Year Map, Region Map, Plant Map

Separated from vin_utils.py to keep each file manageable.
Has NO external dependencies.

Focuses on manufacturers common in the SK/CZ market.
"""

WMI_MAP = {
    # Germany — most common in SK/CZ
    "VW": "Volkswagen", "WV1": "Volkswagen", "WV2": "Volkswagen",
    "WV3": "Volkswagen", "WVW": "Volkswagen", "WVG": "Volkswagen",
    "AUD": "Audi", "TRU": "Audi",
    "WBA": "BMW", "WBS": "BMW", "WBY": "BMW",
    "WDD": "Mercedes-Benz", "WDB": "Mercedes-Benz", "WDC": "Mercedes-Benz",
    "W1K": "Mercedes-Benz", "W1N": "Mercedes-Benz", "W1V": "Mercedes-Benz",
    "W0L": "Opel",
    "WAP": "Porsche", "WP0": "Porsche",
    "WMA": "MAN", "WME": "Smart",
    # France
    "VF1": "Renault", "VF3": "Renault", "VFA": "Renault",
    "VF7": "Citroen",
    "VF9": "Peugeot",
    # Italy
    "ZFA": "Fiat", "ZFC": "Fiat",
    "ZFF": "Ferrari",
    "ZAM": "Maserati", "ZGU": "Maserati",
    "ZHW": "Lamborghini",
    "ZAR": "Alfa Romeo",
    "ZLA": "Lancia",
    # Spain/Czech/Sweden/UK
    "VSS": "Seat", "VS1": "Seat",
    "TMB": "Skoda", "TMP": "Skoda", "TM9": "Skoda",
    "VX1": "Skoda", "VXK": "Skoda",
    "YV1": "Volvo", "YV4": "Volvo", "YVK": "Volvo",
    "SAJ": "Jaguar",
    "SAL": "Land Rover", "SAR": "Land Rover",
    "SCB": "Bentley",
    "SCC": "Lotus",
    # Romania / Russia
    "U9A": "Dacia", "UU1": "Dacia",
    "XTA": "Lada", "XWB": "UAZ",
    # Japan
    "JHM": "Honda", "JH1": "Honda", "JH2": "Honda",
    "JHG": "Honda", "JHL": "Honda",
    "JM1": "Mazda", "JM3": "Mazda",
    "JMB": "Mitsubishi", "JMZ": "Mitsubishi",
    "JN1": "Nissan", "JN3": "Nissan", "JN4": "Nissan",
    "JNA": "Nissan",
    "JS1": "Suzuki", "JS2": "Suzuki", "JS3": "Suzuki",
    "JT1": "Toyota", "JT2": "Toyota", "JT3": "Toyota",
    "JTD": "Toyota", "JTE": "Toyota", "JTF": "Toyota",
    "JTH": "Lexus", "JTJ": "Lexus",
    "JF1": "Subaru", "JF2": "Subaru", "JF3": "Subaru",
    # Korea
    "KMH": "Hyundai", "KLA": "Hyundai",
    "KM8": "Hyundai (USA)",
    "KNA": "Kia", "KNB": "Kia", "KNC": "Kia", "KND": "Kia",
    "KPT": "SsangYong",
    # USA (common imports to SK/CZ)
    "1FA": "Ford (USA)", "1FM": "Ford (USA)", "1FT": "Ford (USA)",
    "1G1": "Chevrolet", "1GC": "Chevrolet",
    "1G6": "Cadillac",
    "1G4": "Buick",
    "1HG": "Honda (USA)",
    "1J4": "Jeep", "1J8": "Jeep",
    "1N4": "Nissan (USA)",
    "1VW": "Volkswagen (USA)",
    "1YV": "Mazda (USA)",
    "2C3": "Chrysler",
    "2T1": "Toyota (Canada)",
    "3VW": "Volkswagen (Mexico)",
    "4S3": "Subaru (USA)", "4S4": "Subaru (USA)",
    "4T1": "Toyota (USA)", "4T3": "Toyota (USA)",
    "4US": "BMW (USA)",
    "5NP": "Hyundai (USA)",
    "5XY": "Kia (USA)",
    "5YJ": "Tesla",
    "1C3": "Chrysler", "1C4": "Chrysler",
    # China (growing presence in EU)
    "L6T": "Geely",
    "LBU": "BYD",
    "LBV": "BMW Brilliance",
    "LFV": "FAW-Volkswagen",
    "LVS": "Ford (China)",
    # India
    "MAA": "Maruti Suzuki",
    "MCA": "Tata",
}

MODEL_YEAR_MAP = {
    'A': 2010, 'B': 2011, 'C': 2012, 'D': 2013, 'E': 2014,
    'F': 2015, 'G': 2016, 'H': 2017, 'J': 2018, 'K': 2019,
    'L': 2020, 'M': 2021, 'N': 2022, 'P': 2023, 'R': 2024,
    'S': 2025, 'T': 2026, 'V': 2027, 'W': 2028, 'X': 2029,
    'Y': 2030, '1': 2031, '2': 2032, '3': 2033, '4': 2034,
    '5': 2035, '6': 2036, '7': 2037, '8': 2038, '9': 2039,
}

MODEL_YEAR_MAP_LEGACY = {
    'A': 1980, 'B': 1981, 'C': 1982, 'D': 1983, 'E': 1984,
    'F': 1985, 'G': 1986, 'H': 1987, 'J': 1988, 'K': 1989,
    'L': 1990, 'M': 1991, 'N': 1992, 'P': 1993, 'R': 1994,
    'S': 1995, 'T': 1996, 'V': 1997, 'W': 1998, 'X': 1999,
    'Y': 2000, '1': 2001, '2': 2002, '3': 2003, '4': 2004,
    '5': 2005, '6': 2006, '7': 2007, '8': 2008, '9': 2009,
}

REGION_MAP = {
    '1': 'USA', '2': 'Canada', '3': 'Mexico',
    '4': 'USA', '5': 'USA',
    '6': 'Oceania', '7': 'Oceania',
    '8': 'South America', '9': 'South America',
    'A': 'Africa', 'B': 'Africa', 'C': 'Africa',
    'D': 'Africa', 'E': 'Africa', 'F': 'Africa',
    'J': 'Japan', 'K': 'Korea',
    'L': 'China', 'M': 'India/SE Asia',
    'N': 'Eurasia', 'P': 'Eurasia', 'R': 'Eurasia',
    'S': 'United Kingdom',
    'T': 'Switzerland',
    'U': 'Europe (other)',
    'V': 'France/Spain',
    'W': 'Germany',
    'X': 'Russia/Eastern Europe',
    'Y': 'Sweden/Scandinavia',
    'Z': 'Italy',
}

PLANT_MAP = {
    "WVW1": "Wolfsburg, Germany",
    "WVW2": "Emden, Germany",
    "WVWA": "Wolfsburg, Germany",
    "WVWB": "Brussels, Belgium",
    "WVWC": "Emden, Germany",
    # WVG Touareg VINs use the 11th character for the assembly plant.
    "WVGD": "Bratislava, Slovakia",
    "1VWB": "Puebla, Mexico",
    "WAUA": "Ingolstadt, Germany",
    "WAUB": "Ingolstadt, Germany",
    "WAUD": "Neckarsulm, Germany",
    "TRUA": "Győr, Hungary",
    "TMBJ": "Mlada Boleslav, Czech Republic",
    "TMBB": "Kvasiny, Czech Republic",
    "JTDK": "Takaoka, Japan",
    "JTEB": "Tahara, Japan",
    "WBAE": "Dingolfing, Germany",
    "WBAD": "Dingolfing, Germany",
    "WBA3": "Munich, Germany",
    "WBA4": "Munich, Germany",
    "WBA1": "Regensburg, Germany",
    "4USC": "Spartanburg, SC, USA",
    "1HGF": "Marysville, OH, USA",
    "1HGC": "Marysville, OH, USA",
    "2HGF": "Alliston, ON, Canada",
    "SHHF": "Swindon, UK",
}
