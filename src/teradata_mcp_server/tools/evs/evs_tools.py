import logging
from teradatasql import TeradataConnection 
from typing import Optional, Any, Dict, List
import json
from datetime import date, datetime
from decimal import Decimal
from teradata_mcp_server.tools.evs_connect import get_evs

logger = logging.getLogger("teradata_mcp_server")

def serialize_teradata_types(obj: Any) -> Any:
    """Convert Teradata-specific types to JSON serializable formats"""
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    return str(obj)

def rows_to_json(cursor_description: Any, rows: List[Any]) -> List[Dict[str, Any]]:
    """Convert database rows to JSON objects using column names as keys"""
    if not cursor_description or not rows:
        return []
    
    columns = [col[0] for col in cursor_description]
    return [
        {
            col: serialize_teradata_types(value)
            for col, value in zip(columns, row)
        }
        for row in rows
    ]

def create_response(data: Any, metadata: Optional[Dict[str, Any]] = None) -> str:
    """Create a standardized JSON response structure"""
    if metadata:
        response = {
            "status": "success",
            "metadata": metadata,
            "results": data
        }
    else:
        response = {
            "status": "success",
            "results": data
        }

    return json.dumps(response, default=serialize_teradata_types)

#------------------ Do not make changes above  ------------------#


#================================================================
#  Enterprise Vector Store tools
#================================================================


def handle_evs_similarity_search(
    conn: TeradataConnection, 
    question: str,
    top_k: int = 1,
    *args,
    **kwargs,
) -> str:

    logger.debug(f"EVS similarity_search: q='{question}', top_k={top_k}")
    vs = get_evs()
    try:
        results = vs.similarity_search(
            question=question,
            top_k=top_k,
            return_type="json",
        )
        return create_response(
            results,
            metadata={
                "tool_name": "evs_similarity_search",
                "question": question,
                "top_k": top_k,
            },
        )
    except Exception as e:
        logger.error(f"EVS similarity_search failed: {e}")
        return json.dumps({"status": "error", "message": str(e)})


# ---------------------------------------------------------------------------
# Common serialization & unified response helpers
# ---------------------------------------------------------------------------
def _serialize_td(obj: Any) -> Any:
    """Serialize Teradata‑specific data types into JSON‑friendly formats."""
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    return str(obj)


def create_response(data: Any, metadata: Optional[Dict[str, Any]] = None) -> str:
    """
    Wrap `data` (and optional `metadata`) in a standard JSON envelope.
    """
    resp = {"status": "success", "results": data}
    if metadata:
        resp["metadata"] = metadata
    return json.dumps(resp, default=_serialize_td)

# ---------------------------------------------------------------------------
# Convert the output of similarity_search() into List[Dict]
# ---------------------------------------------------------------------------
def _materialize(obj: Any) -> List[Dict]:
    """Best‑effort conversion of any return type into a list of dictionaries."""
    # 1) JSON string
    if isinstance(obj, str):
        return json.loads(obj)

    # 2) Already a list / tuple
    if isinstance(obj, (list, tuple)):
        return list(obj)

    # 3) Lazy _SimilaritySearch object: try collect() / result
    for attr in ("collect", "result"):
        if hasattr(obj, attr):
            val = getattr(obj, attr)
            val = val() if callable(val) else val
            return _materialize(val)

    # 4) _SimilaritySearch holding list in an attribute
    for attr in ("similar_objects", "records", "items", "data"):
        if hasattr(obj, attr):
            val = getattr(obj, attr)
            if isinstance(val, list):
                return val

    # 5) teradataml / pandas DataFrame
    if hasattr(obj, "to_pandas"):
        return obj.to_pandas().to_dict(orient="records")

    # 6) Fallback: _SimilaritySearch.to_json()
    if hasattr(obj, "to_json"):
        return json.loads(obj.to_json())

    # 7) Cursor: fetchall() + description
    if hasattr(obj, "fetchall") and hasattr(obj, "description"):
        rows = obj.fetchall()
        cols = [c[0] for c in obj.description]
        return [dict(zip(cols, row)) for row in rows]

    # No branch matched
    raise TypeError(f"Unable to materialize similarity_search result, type={type(obj)}")

# ---------------------------------------------------------------------------
# Main tool: return only the answer text
# ---------------------------------------------------------------------------
def handle_evs_similarity_search_getAnswerOnly(
    conn: TeradataConnection,
    question: str,
    top_k: int = 2,
    faq_tbl: str = "FAQ_DEMO",
    *args, **kwargs,
) -> str:
    """
    1. Run vector search to get top‑k matches.
    2. Pick the kb_id with the highest score.
    3. Look up its answer in the FAQ table.
    4. Return the answer *as‑is* (plain string, no extra JSON wrapper).
    """
    logger.debug("EVS best‑answer: q='%s', top_k=%d", question, top_k)
    vs = get_evs()

    try:
        # 1) Similarity search (keep return_type lowercase 'json' for consistency)
        raw = vs.similarity_search(
            question       = question,
            top_k          = top_k,
            return_type    = "json",
            output_columns = ["kb_id", "score"],
        )

        # 2) Parse into list[dict]
        records = _materialize(raw)
        if not records:
            return "(No similar question found)"

        # 3) Highest‑score kb_id
        best  = max(records, key=lambda r: r["score"])
        kb_id = best["kb_id"]

        # 4) Fetch answer from source table
        sql = f'SELECT answer FROM "{faq_tbl}" WHERE kb_id = ?'
        with conn.cursor() as cur:
            cur.execute(sql, (kb_id,))
            row = cur.fetchone()

        answer = row[0] if row else "(No answer for this kb_id)"
        return answer                                   # ← plain string

    except Exception as e:
        logger.exception("handle_evs_similarity_search_getAnswerOnly failed")
        return json.dumps({"status": "error", "message": str(e)})






