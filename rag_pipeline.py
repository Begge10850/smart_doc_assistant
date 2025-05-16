import boto3
import os
import pdfplumber
from docx import Document
from dotenv import load_dotenv
import pytesseract
from pdf2image import convert_from_path
from PIL import Image
import fitz  # PyMuPDF for annotation extraction

# Load environment variables
load_dotenv()

# Set Tesseract path from .env
pytesseract.pytesseract.tesseract_cmd = os.getenv("TESSERACT_CMD")

# Connect to AWS S3
s3 = boto3.client('s3',
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name="eu-north-1"
)

# Download file from S3
def download_file_from_s3(file_name, download_path):
    try:
        s3.download_file("smart-doc-assistant-saidia", file_name, download_path)
        return True
    except Exception as e:
        print("Download error:", e)
        return False

# Extract annotations/comments from PDF
def extract_annotations_from_pdf(file_path):
    try:
        doc = fitz.open(file_path)
        annotations = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            annot = page.first_annot

            while annot:
                info = annot.info
                if info and "content" in info:
                    annotations.append(info["content"])
                annot = annot.next

        return "\n".join(annotations)

    except Exception as e:
        print("Annotation extraction failed:", e)
        return ""

# Hybrid PDF extraction: try pdfplumber, fallback to OCR if layout is broken or result is weak
def extract_text_from_pdf(file_path):
    text = ""

    # Step 1: Try extracting with pdfplumber
    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                print(f"🧾 Page {page.page_number} preview:", page_text[:200] if page_text else "[No text]")
                if page_text:
                    text += page_text + "\n"
    except Exception as e:
        print("pdfplumber failed:", e)

    # Step 2: Fallback check — force OCR if:
    # - very few words OR
    # - known watermark/pattern like 'essaypro' dominates the page
    text_word_count = len(text.strip().split())
    watermark_hits = text.lower().count("essaypro")
    if text_word_count < 100 or watermark_hits > 3:
        print(f"⚠️ Low quality extract ({text_word_count} words, {watermark_hits} 'essaypro' hits) — using OCR...")
        text = ""
        try:
            images = convert_from_path(file_path)
            for i, img in enumerate(images):
                ocr_text = pytesseract.image_to_string(img)
                print(f"📄 OCR Page {i + 1} preview: {ocr_text[:100]}...")
                text += ocr_text + "\n"
        except Exception as e:
            print("OCR failed:", e)

    # Step 3: Merge annotations if any
    try:
        annotations = extract_annotations_from_pdf(file_path)
        if annotations.strip():
            text += "\n\n[Annotations]\n" + annotations
    except Exception as e:
        print("Annotation merge failed:", e)

    return text

# General handler for different file types
def extract_text_from_file(file_path):
    ext = os.path.splitext(file_path)[-1].lower()

    try:
        if ext == ".txt":
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()

        elif ext == ".pdf":
            return extract_text_from_pdf(file_path)

        elif ext == ".docx":
            doc = Document(file_path)
            text = "\n".join([para.text for para in doc.paragraphs if para.text.strip()])
            print(f"📄 Extracted DOCX content length: {len(text)} characters")
            return text


        else:
            print("Unsupported file format:", ext)
            return ""

    except Exception as e:
        print("Text extraction error:", e)
        return ""
