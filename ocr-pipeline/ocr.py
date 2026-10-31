from paddleocr import PaddleOCR
import os

print("Initializing PaddleOCR engine...")
ocr_engine = PaddleOCR(use_textline_orientation=False, lang='vi')
print("OCR Engine initialized successfully.")

corpus_dir = os.path.abspath("/Users/anhleduc/workspace/rag-finvisor-vn/pack_real/corpus_test")

if not os.path.exists(corpus_dir):
    print(f"Error: Directory '{corpus_dir}' does not exist.")
else:
    pdf_files = [f for f in os.listdir(corpus_dir) if f.lower().endswith('.pdf')]
    
    if not pdf_files:
        print(f"No PDF files found in '{corpus_dir}'.")
    else:
        print(f"Found {len(pdf_files)} PDF file(s) to process.\n")
        
        for filename in pdf_files:
            file_path = os.path.join(corpus_dir, filename)
            print(f"Processing file: {filename}")
            
            try:
                result = ocr_engine.predict(file_path)
                print(f"Completed: {filename}")
                # print(result)  # Uncomment to print extraction details
            except Exception as e:
                print(f"Error processing {filename}: {e}")

        print("\nFinished processing all files.")