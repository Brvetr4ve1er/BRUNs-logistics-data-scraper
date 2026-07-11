import fitz
from contextlib import closing


def extract_text_with_boxes(pdf_path: str):
    """
    Extracts text from a PDF along with its physical bounding box.
    Returns a list of dicts: {"text": str, "bbox": [x0, y0, x1, y1], "page": int}
    """
    extracted_blocks = []

    # closing() releases the native MuPDF handle even if get_text() raises.
    with closing(fitz.open(pdf_path)) as doc:
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            blocks = page.get_text("blocks")

            for block in blocks:
                # block structure: (x0, y0, x1, y1, "text", block_no, block_type)
                if block[6] == 0:  # 0 indicates text block
                    text = block[4].replace("\n", " ").strip()
                    if text:
                        extracted_blocks.append({
                            "text": text,
                            "bbox": [round(block[0], 2), round(block[1], 2), round(block[2], 2), round(block[3], 2)],
                            "page": page_num + 1
                        })

    return extracted_blocks
