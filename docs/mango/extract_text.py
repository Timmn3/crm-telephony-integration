"""Перегоняет PDF-документацию Mango в .txt для быстрого поиска (grep).

Запускать после обновления PDF (Mango правит доку раз в 1-2 месяца):
    .venv/Scripts/python.exe docs/mango/extract_text.py

Каждая страница помечается маркером `===== PAGE N =====`, где N совпадает с
номером страницы PDF. Это позволяет по результату grep сразу открыть нужный
лист оригинала.
"""
from pathlib import Path

from pypdf import PdfReader  # pip install pypdf

DOCS_DIR = Path(__file__).parent


def main() -> None:
    pdfs = sorted(DOCS_DIR.glob("*.pdf"))
    if not pdfs:
        print("PDF не найдены в", DOCS_DIR)
        return

    for src in pdfs:
        reader = PdfReader(str(src))
        dst = src.with_suffix(".txt")
        with dst.open("w", encoding="utf-8") as fh:
            for page_num, page in enumerate(reader.pages, start=1):
                fh.write(f"\n===== PAGE {page_num} =====\n")
                fh.write(page.extract_text() or "")
        print(f"{src.name}: {len(reader.pages)} стр. -> {dst.name}")


if __name__ == "__main__":
    main()
