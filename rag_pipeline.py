import os
import pdfplumber
from docx import Document
import streamlit as st
import fitz  # PyMuPDF for annotation extraction
import boto3
import requests

# ─── AWS CREDENTIALS ────────────────────────────────────────────────────────────
try:
    aws_access_key = st.secrets["aws"]["AWS_ACCESS_KEY_ID"]
    aws_secret_key = st.secrets["aws"]["AWS_SECRET_ACCESS_KEY"]
except Exception:
    from dotenv import load_dotenv
    load_dotenv()
    aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")

s3 = boto3.client(
    's3',
    aws_access_key_id=aws_access_key,
    aws_secret_access_key=aws_secret_key,
    region_name="eu-north-1"
)

# ─── S3 FILE DOWNLOAD ───────────────────────────────────────────────────────────
def download_file_from_s3(file_name, download_path):
    try:
        s3.download_file("smart-doc-assistant-saidia", file_name, download_path)
        return True
    except Exception as e:
        print("Download error:", e)
        return False

# ─── OCR.SPACE FALLBACK ─────────────────────────────────────────────────────────
def extract_text_with_ocr_space(file_path):
    try:
        api_key = st.secrets["ocr_space"]["OCR_API_KEY"]
        with open(file_path, 'rb') as f:
            response = requests.post(
                'https://api.ocr.space/parse/image',
                files={'file': f},
                data={'apikey': api_key, 'language': 'eng'}
            )
        result = response.json()
        print("📡 OCR.Space fallback triggered...")
        return result["ParsedResults"][0]["ParsedText"]
    except Exception as e:
        print("🛑 OCR.Space error:", e)
        return ""

# ─── ANNOTATION EXTRACTION FROM PDF ─────────────────────────────────────────────
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

# ─── TEXT EXTRACTION FROM PDF ───────────────────────────────────────────────────
def extract_text_from_pdf(file_path):
    text = ""

    # Step 1: Try pdfplumber
    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                print(f"🧾 Page {page.page_number} preview:", page_text[:200] if page_text else "[No text]")
                if page_text:
                    text += page_text + "\n"
    except Exception as e:
        print("pdfplumber failed:", e)

    # Step 2: Check if fallback needed
    text_word_count = len(text.strip().split())
    watermark_hits = text.lower().count("essaypro")

    if text_word_count < 100 or watermark_hits > 3:
        print(f"⚠️ Low quality extract ({text_word_count} words, {watermark_hits} 'essaypro' hits) — using OCR.Space fallback...")
        text = extract_text_with_ocr_space(file_path)

    # Step 3: Merge annotations
    try:
        annotations = extract_annotations_from_pdf(file_path)
        if annotations.strip():
            text += "\n\n[Annotations]\n" + annotations
    except Exception as e:
        print("Annotation merge failed:", e)

    return text

# ─── MAIN HANDLER ───────────────────────────────────────────────────────────────
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
