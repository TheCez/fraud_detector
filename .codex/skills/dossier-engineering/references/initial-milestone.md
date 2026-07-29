# Initial Milestone Reference

The product is a local-first two-page audit-dossier demo: upload one ZIP, then review its files, normalized data, deterministic findings, reasoning, and exact evidence.

Stack: React, TypeScript, Vite, Tailwind; Python 3.12+, FastAPI, Pydantic; pandas, openpyxl, python-docx, PyMuPDF; local filesystem and SQLite; pytest and Vitest/React Testing Library.

Classify accounting TXT/CSV/XLSX as evidence; business PDF/DOCX as supporting; `index.xml` and GDPdU DTD as technical metadata. Retain all files in the manifest. Never use OCR, cloud infrastructure, authentication, queues, vector databases, or source-document editing in the demo milestone.

The normalized output is JSONL plus SQLite. The canonical record envelope includes record and document IDs, a record type, source provenance, original data, normalized data, and search text. Preserve all raw fields, normalize only confident dates and German decimals, and leave IDs and legal terms intact.

The sample dossier contains German ledger, vendor, customer, fixed-asset, and supporting-document folders. Expected formats are TXT, CSV, XLSX, DOCX, PDF, XML, and DTD.
