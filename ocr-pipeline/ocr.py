from paddleocr import PaddleOCR  # PaddleOCR for scanned PDFs/images

ocr_engine = PaddleOCR(use_textline_orientation=False, lang='vi')

print(ocr_engine)

