"""Fictional companies and catalogs for the synthetic corpus. None of them exist.

Addresses use only Latin-1 characters: the PDF renderer uses the standard Type 1 fonts.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Product:
    sku: str
    description: str
    unit: str
    price: float


@dataclass(frozen=True)
class Company:
    name: str
    short: str
    address: tuple[str, ...]
    country: str
    tax_id: str | None = None
    bank: str | None = None  # IBAN-like, only some vendors print it


@dataclass(frozen=True)
class Vendor:
    company: Company
    currency: str  # USD | EUR | GBP
    invoice_prefix: str  # how this vendor numbers invoices, deliberately varied
    tax_rate: float  # what this vendor charges the UK buyer
    tax_label: str
    terms: str
    catalog: tuple[Product, ...] = field(default_factory=tuple)


BUYER = Company(
    name="Oakridge Fabrication Works Ltd",
    short="Oakridge Fabrication",
    address=("48 Mill Lane", "Sheffield S9 2XT", "United Kingdom"),
    country="UK",
    tax_id="GB 447 2210 83",
)

TERMS_DAYS = {"Net 30": 30, "Net 14": 14, "Net 45": 45, "Due on receipt": 0, "2/10 Net 30": 30}

VENDORS: tuple[Vendor, ...] = (
    Vendor(
        Company("Brightwater Fasteners Inc.", "Brightwater Fasteners",
                ("1420 Industrial Pkwy", "Toledo, OH 43605", "USA"), "US", tax_id="EIN 38-4471120"),
        "USD", "INV-2025-", 0.0, "Sales tax (exempt, resale)", "Net 30",
        (
            Product("BF-1001", "Hex bolt M8x40, zinc plated, box of 100", "box", 18.40),
            Product("BF-1002", "Hex nut M8, zinc plated, box of 200", "box", 9.75),
            Product("BF-1010", "Flat washer M8, box of 500", "box", 12.30),
            Product("BF-2001", "Self-tapping screw 4.2x25, box of 1000", "box", 27.90),
            Product("BF-3005", "Threaded rod M10, 1 m", "pc", 4.15),
            Product("BF-4100", "Anchor bolt M12x120, box of 25", "box", 31.60),
        ),
    ),
    Vendor(
        Company("Kesselring Stahlhandel GmbH", "Kesselring Stahlhandel",
                ("Hafenstrasse 112", "47119 Duisburg", "Germany"), "DE",
                tax_id="DE 311 448 902", bank="DE44 3505 0000 0123 4567 89"),
        "EUR", "KS-RE-25-", 0.0, "VAT 0% (reverse charge)", "Net 14",
        (
            Product("KS-S235-2", "Steel sheet S235JR 2.0 mm, 1000x2000", "sheet", 61.20),
            Product("KS-RHS-40", "Rectangular hollow section 40x40x3, 6 m", "bar", 48.90),
            Product("KS-FB-50", "Flat bar 50x5, 6 m", "bar", 22.35),
            Product("KS-ANG-30", "Angle 30x30x3, 6 m", "bar", 17.80),
            Product("KS-PIPE-33", "Round tube 33.7x2.6, 6 m", "bar", 29.45),
        ),
    ),
    Vendor(
        Company("Northgate Coatings Ltd", "Northgate Coatings",
                ("Unit 7, Kirkstall Trading Estate", "Leeds LS5 3BT", "United Kingdom"), "UK",
                tax_id="GB 902 1177 45", bank="GB29 NWBK 6016 1331 9268 19"),
        "GBP", "NC/2025/", 0.20, "VAT 20%", "Net 30",
        (
            Product("NC-PR-5L", "Zinc phosphate primer, grey, 5 L", "can", 42.50),
            Product("NC-TC-5L", "Topcoat RAL 7035, satin, 5 L", "can", 56.00),
            Product("NC-TH-1L", "Thinner No. 2, 1 L", "can", 7.80),
            Product("NC-ROLL", "Roller kit 230 mm", "kit", 11.25),
            Product("NC-MSK", "Masking tape 48 mm x 50 m", "roll", 3.40),
        ),
    ),
    Vendor(
        Company("Vanderlaan Packaging B.V.", "Vanderlaan Packaging",
                ("Waalhaven Zuidzijde 19", "3089 JH Rotterdam", "Netherlands"), "NL",
                tax_id="NL 8123.45.678.B01"),
        "EUR", "2025-", 0.0, "VAT 0% (intra-community supply)", "Net 30",
        (
            Product("VP-BOX-M", "Corrugated box 400x300x300, double wall, bundle of 25", "bundle", 38.75),
            Product("VP-BOX-L", "Corrugated box 600x400x400, double wall, bundle of 20", "bundle", 46.00),
            Product("VP-STR", "Stretch film 500 mm x 300 m, 23 my", "roll", 9.60),
            Product("VP-PAL", "Euro pallet 1200x800, new", "pc", 14.90),
            Product("VP-TAPE", "Packing tape 48 mm x 66 m, brown, pack of 36", "pack", 24.30),
        ),
    ),
    Vendor(
        Company("Moravia Tooling s.r.o.", "Moravia Tooling",
                ("Videnska 204", "619 00 Brno", "Czech Republic"), "CZ",
                tax_id="CZ 27419581", bank="CZ65 0800 0000 1920 0014 5399"),
        "EUR", "FV-2025-", 0.0, "VAT 0% (reverse charge)", "Net 45",
        (
            Product("MT-END-10", "Carbide end mill 10 mm, 4 flutes, AlTiN", "pc", 34.20),
            Product("MT-DRL-SET", "HSS drill set 1-13 mm, 25 pcs", "set", 58.90),
            Product("MT-INS-CNMG", "Turning insert CNMG 120408, pack of 10", "pack", 71.50),
            Product("MT-TAP-M8", "Machine tap M8, HSS-E", "pc", 12.60),
            Product("MT-VISE", "Machine vise 125 mm", "pc", 189.00),
        ),
    ),
    Vendor(
        Company("Pinecrest Safety Supply LLC", "Pinecrest Safety",
                ("2210 NW Yeon Ave", "Portland, OR 97210", "USA"), "US", tax_id="EIN 93-2208741"),
        "USD", "PSS-", 0.0, "Sales tax (exempt, resale)", "Due on receipt",
        (
            Product("PS-GLV-L", "Nitrile gloves, size L, box of 100", "box", 8.95),
            Product("PS-GOG", "Safety goggles, anti-fog", "pc", 6.40),
            Product("PS-EAR", "Foam ear plugs, box of 200 pairs", "box", 21.00),
            Product("PS-HV-VEST", "Hi-vis vest, class 2, orange", "pc", 5.75),
            Product("PS-RESP", "Half-mask respirator, medium", "pc", 27.30),
            Product("PS-FLT-P3", "P3 particulate filter, pair", "pair", 9.10),
        ),
    ),
    Vendor(
        Company("Aldermoor Electrical Components Ltd", "Aldermoor Electrical",
                ("14 Feeder Road", "Bristol BS2 0SB", "United Kingdom"), "UK",
                tax_id="GB 655 0094 21", bank="GB76 BARC 2003 5312 4486 70"),
        "GBP", "AEC-", 0.20, "VAT 20%", "Net 30",
        (
            Product("AE-REL-24", "Relay 24 V DC, 10 A, DIN rail", "pc", 7.85),
            Product("AE-TB-2.5", "Terminal block 2.5 mm2, grey, pack of 50", "pack", 16.20),
            Product("AE-CBL-1.5", "Cable 3G1.5 mm2, 100 m drum", "drum", 64.00),
            Product("AE-MCB-16", "MCB 16 A, curve C, 1P", "pc", 4.95),
            Product("AE-ENC-300", "Steel enclosure 300x400x200, IP66", "pc", 88.50),
        ),
    ),
    Vendor(
        Company("Sierra Desk & Paper Co.", "Sierra Desk & Paper",
                ("455 S Virginia St", "Reno, NV 89501", "USA"), "US", tax_id="EIN 88-1930412"),
        "USD", "SDP", 0.0825, "Sales tax 8.25%", "Net 14",
        (
            Product("SD-PAP-A4", "Copy paper A4 80 g, box of 5 reams", "box", 29.90),
            Product("SD-TNR-83", "Toner cartridge 83A, black", "pc", 74.00),
            Product("SD-PEN", "Ballpoint pens, blue, box of 50", "box", 11.50),
            Product("SD-FLD", "Manila folders, letter, box of 100", "box", 15.80),
            Product("SD-LBL", "Shipping labels 4x6, roll of 500", "roll", 19.95),
        ),
    ),
    Vendor(
        Company("Hallstrom Hydraulik AB", "Hallstrom Hydraulik",
                ("Marieholmsgatan 10", "415 02 Goteborg", "Sweden"), "SE",
                tax_id="SE 556677889901", bank="SE35 5000 0000 0549 1000 0003"),
        "EUR", "HH-", 0.0, "VAT 0% (reverse charge)", "Net 30",
        (
            Product("HH-HOSE-12", "Hydraulic hose 1/2 inch, 2SN, 10 m", "pc", 57.40),
            Product("HH-FIT-JIC8", "Fitting JIC-8 male, straight", "pc", 3.95),
            Product("HH-PUMP-20", "Gear pump 20 cc/rev, group 2", "pc", 212.00),
            Product("HH-OIL-46", "Hydraulic oil ISO VG 46, 20 L", "can", 68.30),
            Product("HH-SEAL", "Cylinder seal kit, 63 mm", "kit", 24.75),
        ),
    ),
    Vendor(
        Company("Riverbend Lumber & Millwork", "Riverbend Lumber",
                ("3300 Amnicola Hwy", "Chattanooga, TN 37406", "USA"), "US", tax_id="EIN 62-7730918"),
        "USD", "RLM-", 0.0, "Sales tax (exempt, resale)", "2/10 Net 30",
        (
            Product("RL-PLY-18", "Plywood 18 mm, 1220x2440, BB/CC", "sheet", 46.20),
            Product("RL-2X4", "Lumber 2x4, 8 ft, SPF", "pc", 3.85),
            Product("RL-MDF-12", "MDF 12 mm, 1220x2440", "sheet", 31.70),
            Product("RL-OAK-25", "Oak board 25 mm, per m2", "m2", 72.00),
            Product("RL-GLUE", "Wood glue, 1 gal", "pc", 18.60),
        ),
    ),
    Vendor(
        Company("Lindqvist Logistics Oy", "Lindqvist Logistics",
                ("Satamakatu 4", "00160 Helsinki", "Finland"), "FI", tax_id="FI 22114455"),
        "EUR", "LL", 0.0, "VAT 0% (reverse charge)", "Net 14",
        (
            Product("LL-FRT-PAL", "Road freight, per pallet, DE/NL to Sheffield", "pallet", 94.00),
            Product("LL-FRT-EXP", "Express surcharge, per shipment", "shipment", 120.00),
            Product("LL-CUST", "Customs handling, per declaration", "declaration", 45.00),
            Product("LL-INS", "Transit insurance, per shipment", "shipment", 18.50),
        ),
    ),
    Vendor(
        Company("Cobalt Ridge Electronics", "Cobalt Ridge Electronics",
                ("7801 N Capital of Texas Hwy", "Austin, TX 78731", "USA"), "US", tax_id="EIN 74-3351902"),
        "USD", "CRE-INV-", 0.0, "Sales tax (exempt, resale)", "Net 30",
        (
            Product("CR-PSU-24", "Power supply 24 V DC, 10 A, DIN rail", "pc", 86.00),
            Product("CR-SEN-IR", "Inductive proximity sensor M18, PNP", "pc", 23.40),
            Product("CR-HMI-7", "HMI panel 7 inch, Ethernet", "pc", 412.00),
            Product("CR-PLC-S", "Compact PLC, 14 DI / 10 DO", "pc", 298.00),
            Product("CR-CBL-ETH", "Ethernet patch cable Cat6, 5 m", "pc", 4.10),
        ),
    ),
)

# The freight forwarder invoices without a purchase order.
NO_PO_VENDORS = {"Lindqvist Logistics Oy"}

NOTES = (
    "Goods remain the property of the seller until paid in full.",
    "Deliver to Gate B, Mon-Fri 08:00-16:00. Call 30 minutes before arrival.",
    "Partial delivery: items marked * ship separately in week 32.",
    "Early payment discount 2% if settled within 10 days of invoice date.",
    "Quotation reference Q-2025-118 applies. Prices firm until 31 Dec 2025.",
    "Packing list and CE declarations enclosed with the shipment.",
    "Please quote the document number on all correspondence and remittances.",
    "Pallets are returnable; a credit of 7.50 per pallet is issued on return.",
)
