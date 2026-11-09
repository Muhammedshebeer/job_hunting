import fitz
def extract_pdf_text(file_path):
    with fitz.open(file_path) as doc:
        return '\n'.join(page.get_text() for page in doc)
