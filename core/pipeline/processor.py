from .job import Job, JobStatus
from ..extraction.llm_client import LLMClient
from ..extraction.text_extractor import extract_text
from ..extraction.chunker import chunk_pdf_text, merge_chunk_results
from ..validation.engine import validate_extraction
from ..storage.repository import insert_document
import traceback
import json
import hashlib
import os
from ..storage.db import get_connection
from ..search.vector_db import VectorSearchEngine

class PipelineProcessor:
    def __init__(self, llm_client: LLMClient, db_path: str):
        self.llm_client = llm_client
        self.db_path = db_path
        
    def _extract_with_retry(self, text: str, module: str, doc_type: str, job: Job):
        """Run LLM extraction with automatic retry on failure."""
        max_retries = 2
        for attempt in range(max_retries):
            try:
                result = self.llm_client.extract(text, module=module, doc_type=doc_type)
                job.log(f"LLM extraction successful on attempt {attempt + 1}.")
                return result
            except Exception as e:
                job.log(f"Extraction failed on attempt {attempt + 1}: {e}")
                job.retries += 1
                if attempt == max_retries - 1:
                    raise e

    def process_file(self, file_path: str, module: str, doc_type: str = "UNKNOWN") -> Job:
        job = Job(type="DOCUMENT_EXTRACTION", input_data={"file_path": file_path, "module": module, "doc_type": doc_type})
        job.status = JobStatus.PROCESSING
        job.log(f"Started processing file: {file_path}")

        # TD4 fix: hold the connection in a name visible to `finally` so we
        # always close it — even when extraction crashes mid-pipeline. Under
        # load, leaking SQLite connections exhausts file handles and
        # eventually blocks new uploads.
        conn = None
        try:
            # ── Step 0: Hash for deduplication ──────────────────────────────
            job.log(f"Hashing file...")
            file_hash = hashlib.sha256()
            with open(file_path, 'rb') as f:
                while chunk := f.read(8192):
                    file_hash.update(chunk)
            file_hash_hex = file_hash.hexdigest()

            conn = get_connection(self.db_path)
            cached = conn.execute(
                "SELECT result_json FROM extraction_cache WHERE file_hash=?",
                (file_hash_hex,)
            ).fetchone()
            
            cached_data = None
            if cached:
                try:
                    parsed = json.loads(cached["result_json"])
                    if isinstance(parsed, dict):
                        cached_data = parsed
                except (json.JSONDecodeError, TypeError):
                    cached_data = None  # corrupt cache row → fall through to re-extract

            if cached_data is not None:
                job.log("Cache hit — bypassing LLM extraction.")
                from core.extraction.llm_client import _confidence
                extracted_data    = cached_data
                full_text         = "Cached extraction"
                doc_type_resolved = doc_type
                # Recompute confidence from the cached payload instead of
                # hardcoding 1.0 — a cached low-confidence result should still
                # route to the review queue.
                confidence        = _confidence(module, extracted_data)
            else:
                # ── Step 1: Chunked Extraction (Map-Reduce) ──────────────────
                is_pdf = file_path.lower().endswith(".pdf")
                
                if is_pdf:
                    job.log("PDF detected — running chunked Map-Reduce extraction.")
                    chunks = chunk_pdf_text(file_path)
                    job.log(f"Split into {len(chunks)} chunk(s).")
                    
                    chunk_results = []
                    for i, chunk in enumerate(chunks):
                        job.log(f"Extracting chunk {i+1}/{len(chunks)} (pages {chunk['pages']})...")
                        result = self._extract_with_retry(chunk["text"], module, doc_type, job)
                        chunk_results.append(result.data)
                    
                    job.log("Merging chunk results...")
                    extracted_data    = merge_chunk_results(chunk_results)
                    full_text         = " ".join(c["text"] for c in chunks)
                    doc_type_resolved = doc_type
                    confidence        = 0.95
                else:
                    # Non-PDF: single-pass extraction
                    job.log("Running text extractor (single-pass)...")
                    full_text = extract_text(file_path)
                    if not full_text:
                        raise ValueError("No text extracted from document.")
                    result            = self._extract_with_retry(full_text, module, doc_type, job)
                    extracted_data    = result.data
                    doc_type_resolved = result.doc_type
                    confidence        = result.confidence

                # Save to cache
                conn.execute(
                    "INSERT OR REPLACE INTO extraction_cache (file_hash, result_json) VALUES (?, ?)",
                    (file_hash_hex, json.dumps(extracted_data))
                )
                conn.commit()
            
            # ── Step 2: Validation ───────────────────────────────────────────
            job.log("Running validation...")
            validation_result = validate_extraction(extracted_data, module=module)
            job.log(
                f"Validation done — Errors: {validation_result['error_count']}, "
                f"Warnings: {validation_result['warning_count']}"
            )
            
            # ── Step 3: Persist to Storage ───────────────────────────────────
            job.log("Saving document to repository...")
            doc_data = {
                "type":           doc_type_resolved,
                "raw_text":       full_text,
                "extracted_json": json.dumps(extracted_data),
                "confidence":     confidence,
                "source_file":    file_path,
                "module":         module
            }
            doc_id = insert_document(self.db_path, doc_data)
            job.log(f"Document saved with ID: {doc_id}")
            
            # ── Step 4: Vector Embed ─────────────────────────────────────────
            try:
                job.log("Embedding document for Semantic Search...")
                db_dir        = os.path.dirname(self.db_path)
                vector_db_dir = os.path.join(db_dir, "vector")
                vector_db     = VectorSearchEngine(vector_db_dir)
                vector_db.embed_document(doc_id, module, full_text, extracted_data)
                job.log("Vector embedding complete.")
            except Exception as v_err:
                job.log(f"Warning: Vector embedding failed (non-fatal): {v_err}")
            
            job.complete({
                "document_id":    doc_id,
                "extracted_data": extracted_data,
                "validation":     validation_result
            })

        except Exception as e:
            job.log(f"Error during processing: {str(e)}")
            job.log(traceback.format_exc())
            job.fail(str(e))
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

        return job
