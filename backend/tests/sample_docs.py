"""Generate small, synthetic clinical documents (PDF + Word .docx) for tests and demos.

All people, IDs and numbers are fictitious. No third-party libraries are needed.
Run `python -m tests.sample_docs <out_dir>` to write the demo files.
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

REFERRAL_LINES = [
    "SPINE & JOINT CLINIC - REQUEST FOR PRIOR AUTHORIZATION",
    "Patient Name: Meera Krishnan",
    "Date of Birth: 14/02/1985",
    "Member ID: W318840027",
    "Address: 22 Lake View Road, Adyar, Chennai, TN 600020",
    "Requesting Provider NPI: 1234567893",
    "History of present illness: 12 weeks of low back pain radiating to the right leg.",
    "Failed 8 weeks of physiotherapy and NSAIDs.",
    "Exam: positive straight leg raise; progressive weakness of ankle dorsiflexion.",
    "Assessment: M54.17 lumbosacral radiculopathy; M51.27 disc displacement.",
    "Plan: MRI lumbar spine without contrast. CPT: 72148",
    "Urgent review requested: progressive motor deficit risks permanent loss of function.",
]

DENIAL_PARAS = [
    "NOTICE OF ADVERSE DETERMINATION",
    "Reference: PA-771204",
    "Patient Name: Meera Krishnan",
    "Member ID: W318840027",
    "Your request for MRI lumbar spine (CPT: 72148) was denied as not medically necessary.",
    "You have appeal rights within 60 days of this notice.",
]


def make_pdf(lines: list[str]) -> bytes:
    def esc(t: str) -> str:
        return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = "BT /F1 11 Tf 50 760 Td 15 TL\n" + "\n".join(f"({esc(l)}) '" for l in lines) + "\nET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode("latin-1")


def make_docx(paragraphs: list[str]) -> bytes:
    body = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{escape(p)}</w:t></w:r></w:p>" for p in paragraphs)
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/document.xml": f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>',
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def write_samples(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    items = {
        "referral_meera_krishnan.pdf": make_pdf(REFERRAL_LINES),
        "referral_meera_krishnan.docx": make_docx(REFERRAL_LINES),
        "denial_letter_PA-771204.docx": make_docx(DENIAL_PARAS),
    }
    paths = []
    for name, data in items.items():
        p = out / name
        p.write_bytes(data)
        paths.append(p)
    return paths


if __name__ == "__main__":
    for p in write_samples(Path(sys.argv[1] if len(sys.argv) > 1 else "sample-documents")):
        print("wrote", p)


# ---------------------------------------------------------------------------------------------
# Five end-to-end scenarios — one PDF each, covering every request type and service category.
# ---------------------------------------------------------------------------------------------
SCENARIOS: dict[str, list[str]] = {
    "case1_prior_auth_knee_arthroscopy.pdf": [
        "APOLLO ORTHO CENTRE - REQUEST FOR PRIOR AUTHORIZATION",
        "Patient Name: Arjun Mehta",
        "Date of Birth: 03/07/1978",
        "Member ID: W410227315",
        "Address: 45 MG Road, Indiranagar, Bengaluru, KA 560038",
        "Requesting Provider NPI: 1234567893",
        "History of present illness: right knee locking and pain for 5 months after a sports injury.",
        "Exam: positive McMurray test, joint line tenderness. MRI confirms bucket-handle tear.",
        "Assessment: S83.241A medial meniscus tear; M23.205 derangement of meniscus.",
        "Plan: day-case knee arthroscopy with partial meniscectomy. CPT: 29881",
        "Progress note signed by treating orthopaedic surgeon.",
    ],
    "case2_concurrent_review_pneumonia.pdf": [
        "CITY GENERAL HOSPITAL - CONCURRENT REVIEW / CONTINUED STAY",
        "Patient Name: Fatima Sheikh",
        "Date of Birth: 21/11/1956",
        "Member ID: W552019874",
        "Address: 9 Marine Lines, Mumbai, MH 400002",
        "Attending Provider NPI: 1093817462",
        "Admission Date: 15/11/2026",
        "Requested length of stay: 6 days",
        "History of present illness: community acquired pneumonia with hypoxia on admission.",
        "Assessment: J18.9 pneumonia; E11.9 type 2 diabetes. Progress note: on IV antibiotics.",
        "Plan: continue inpatient care, oxygen weaning, review in 48 hours.",
    ],
    "case3_retro_review_behavioral_health.pdf": [
        "MINDCARE CLINIC - RETROSPECTIVE REVIEW REQUEST",
        "Patient Name: Rohan Desai",
        "Date of Birth: 08/09/1990",
        "Member ID: W667120458",
        "Address: 17 FC Road, Shivajinagar, Pune, MH 411005",
        "Treating Provider NPI: 1427395060",
        "Date of Service: 02/10/2026",
        "History of present illness: recurrent depressive disorder, moderate episode.",
        "Assessment: F33.1 major depressive disorder, recurrent. Progress note attached.",
        "Plan: psychiatric evaluation and psychotherapy session were provided. CPT: 90837",
    ],
    "case4_prior_auth_cpap_rental.pdf": [
        "SLEEP WELL CENTRE - REQUEST FOR AUTHORIZATION (DME)",
        "Patient Name: Lakshmi Narayanan",
        "Date of Birth: 30/01/1969",
        "Member ID: W778341260",
        "Address: 3 Race Course Road, Coimbatore, TN 641018",
        "Prescribing Provider NPI: 1316284052",
        "History of present illness: loud snoring, witnessed apnoeas, daytime sleepiness.",
        "Sleep study: AHI 34 events per hour. Assessment: G47.33 obstructive sleep apnoea.",
        "Plan: CPAP device rental for 3 months with compliance review. Progress note on file.",
    ],
    "case5_appeal_ct_head_urgent.pdf": [
        "NEURO CARE INSTITUTE - APPEAL OF ADVERSE DETERMINATION",
        "Patient Name: Sanjay Kulkarni",
        "Date of Birth: 12/05/1972",
        "Member ID: W889452371",
        "Address: 88 Residency Road, Hyderabad, TS 500001",
        "Requesting Provider NPI: 1508962739",
        "Original authorization PA-605118 was denied for insufficient documentation.",
        "History of present illness: new severe headache with vomiting and left arm weakness.",
        "Assessment: G43.909 migraine vs R51.9 headache; rule out intracranial bleed.",
        "Plan: urgent CT head without contrast. CPT: 70450",
        "Physician attestation: delay could jeopardize the member's life. Urgent review requested.",
    ],
}


def write_scenarios(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, lines in SCENARIOS.items():
        p = out / name
        p.write_bytes(make_pdf(lines))
        paths.append(p)
    return paths
