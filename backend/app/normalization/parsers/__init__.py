"""GDPdU and document parsers for the normalization pipeline."""

from app.normalization.parsers.csv_parser import parse_csv_file
from app.normalization.parsers.docx_parser import parse_docx_file
from app.normalization.parsers.gdpdu_txt import (
    convert_german_date,
    convert_german_decimal,
    parse_gdpdu_folder,
    parse_index_xml,
)
from app.normalization.parsers.pdf_parser import parse_pdf_file
from app.normalization.parsers.xlsx_parser import parse_xlsx_file
from app.normalization.parsers.xml_parser import parse_xml_file

__all__ = [
    "convert_german_date",
    "convert_german_decimal",
    "parse_csv_file",
    "parse_docx_file",
    "parse_gdpdu_folder",
    "parse_index_xml",
    "parse_pdf_file",
    "parse_xlsx_file",
    "parse_xml_file",
]
