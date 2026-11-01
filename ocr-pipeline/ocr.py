from paddleocr import PaddleOCR
import os
import json

print("Initializing PaddleOCR engine...")
ocr_engine = PaddleOCR(use_textline_orientation=False, lang='vi')
print("OCR Engine initialized successfully.")

corpus_dir = os.path.abspath("/Users/anhleduc/workspace/rag-finvisor-vn/pack_real/corpus_test")
output_dir = os.path.join(os.path.dirname(corpus_dir), "corpus_test_output")
os.makedirs(output_dir, exist_ok=True)

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
                print(result)
                assert False
                
                base_name = os.path.splitext(filename)[0]
                output_json_path = os.path.join(output_dir, f"{base_name}_ocr.json")
                
                with open(output_json_path, 'w', encoding='utf-8') as f:
                    json.dump(result, f, ensure_ascii=False, indent=4)
                
                print(f"Completed & saved JSON: {filename} -> {os.path.basename(output_json_path)}")
                
                output_txt_path = os.path.join(output_dir, f"{base_name}_ocr.txt")
                with open(output_txt_path, 'w', encoding='utf-8') as f_txt:
                    for line in result:
                        if isinstance(line, list):
                            for box in line:
                                if len(box) >= 2 and len(box[1]) >= 1:
                                    text = box[1][0]
                                    f_txt.write(text + "\n")
                        elif isinstance(line, dict):
                            pass
                print(f"Saved TXT text content: {os.path.basename(output_txt_path)}")
                
            except Exception as e:
                print(f"Error processing {filename}: {e}")

        print(f"\nFinished processing all files. Results saved in: {output_dir}")