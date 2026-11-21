"""FreshGuard: stale-answer detector.

Flow: text -> extract time-sensitive claims (cheap model) -> Tavily live search
-> judge (strong model) -> freshness score per claim.
All LLM calls go through the Bifrost gateway (OpenAI-compatible API).
"""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from openai import OpenAI
from pydantic import BaseModel
from tavily import TavilyClient

load_dotenv()

BIFROST_URL = os.getenv("BIFROST_URL", "http://localhost:8080/v1")
EXTRACTOR_MODEL = os.getenv("EXTRACTOR_MODEL", "anthropic/claude-haiku-5-5")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "anthropic/claude-sonnet-5-5")
ANSWER_MODEL = os.getenv("ANSWER_MODEL", EXTRACTOR_MODEL)  # the "chatbot" that may be stale

# The provider key lives only inside Bifrost, so the app uses a dummy key.
llm = OpenAI(base_url=BIFROST_URL, api_key="not-needed")
tavily = TavilyClient(api_key=os.environ.get("TAVILY_API_KEY", "missing"))

app = FastAPI(title="FreshGuard")


class CheckRequest(BaseModel):
    text: str
    max_claims: int = 6


def ask_json(model: str, system: str, user: str):
    """Call a model through Bifrost and parse a JSON reply."""
    resp = llm.chat.completions.create(
        model=model,
        temperature=0,
        max_tokens=1200,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    raw = resp.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    return json.loads(raw)


def extract_claims(text: str, max_claims: int):
    system = (
        "You find time-sensitive factual claims in text: prices, software or API "
        "versions, laws and regulations, company leadership, statistics, product "
        "availability. Ignore timeless facts. Reply ONLY with a JSON list; each item: "
        '{"claim": str, "type": "price|version|law|leadership|stat|other", '
        '"volatility": "high|medium|low", "search_query": str}. '
        f"At most {max_claims} items, most volatile first."
    )
    return ask_json(EXTRACTOR_MODEL, system, text)


def gather_evidence(claim: dict):
    result = tavily.search(
        query=claim["search_query"],
        search_depth="advanced",
        max_results=5,
        exclude_domains=["reddit.com", "facebook.com", "quora.com", "tiktok.com", "pinterest.com"],
    )
    return [
        {
            "title": r.get("title"),
            "url": r.get("url"),
            "content": (r.get("content") or "")[:700],
            "published": r.get("published_date"),
        }
        for r in result.get("results", [])
    ]


def judge(claim: dict, evidence: list):
    system = (
        f"Today is {date.today().isoformat()}. You check whether a claim is still "
        "true using ONLY the evidence given. Prefer newer and official sources. "
        "Reply ONLY with JSON: "
        '{"verdict": "current|outdated|contradicted|unverifiable", '
        '"confidence": number 0-1, "corrected_fact": str or null, '
        '"best_source_url": str or null, "reason": str}. '
        "If evidence is weak or missing, use unverifiable. Never invent facts."
    )
    user = json.dumps({"claim": claim["claim"], "evidence": evidence})
    return ask_json(JUDGE_MODEL, system, user)


def freshness_score(verdict: str, confidence: float) -> int:
    """0-100. Higher = more likely still accurate."""
    confidence = max(0.0, min(1.0, confidence))
    if verdict == "current":
        return round(50 + 50 * confidence)
    if verdict in ("outdated", "contradicted"):
        return round(25 * (1 - confidence))
    return 40  # unverifiable: neither trusted nor flagged as wrong


def check_claim(claim: dict) -> dict:
    """Verify one claim. Never raises: a failure becomes 'unverifiable'."""
    try:
        evidence = gather_evidence(claim)
        verdict = judge(claim, evidence)
        sources = [e["url"] for e in evidence]
    except Exception as exc:  # network, JSON or model error
        verdict = {
            "verdict": "unverifiable",
            "confidence": 0,
            "corrected_fact": None,
            "best_source_url": None,
            "reason": f"Check failed: {exc}",
        }
        sources = []
    score = freshness_score(verdict["verdict"], float(verdict.get("confidence", 0)))
    return {**claim, **verdict, "score": score, "sources": sources}


def run_check(text: str, max_claims: int = 6) -> dict:
    try:
        claims = extract_claims(text, max_claims)
    except Exception as exc:  # e.g. Bifrost not running
        return {"checked_on": date.today().isoformat(), "claims": [],
                "error": f"Failed to extract claims: {exc}"}
    with ThreadPoolExecutor(max_workers=3) as pool:  # claims are independent
        results = list(pool.map(check_claim, claims))  # keeps the original order
    return {"checked_on": date.today().isoformat(), "claims": results}


@app.post("/check")
def check(req: CheckRequest):
    return run_check(req.text, req.max_claims)


class AskRequest(BaseModel):
    question: str
    max_claims: int = 5


def answer_question(question: str) -> str:
    """The 'AI app' whose answer we verify. Uses the cheap model through Bifrost."""
    resp = llm.chat.completions.create(
        model=ANSWER_MODEL,
        temperature=0,
        max_tokens=500,
        messages=[
            {"role": "system", "content": "Answer in 3-5 sentences. Be specific: give "
             "versions, prices, dates and names where relevant."},
            {"role": "user", "content": question},
        ],
    )
    return resp.choices[0].message.content.strip()


def fix_answer(question: str, answer: str, claims: list) -> str:
    """Rewrite the answer using only the verified corrections. No stale claims -> unchanged."""
    stale = [c for c in claims
             if c.get("verdict") in ("outdated", "contradicted") and c.get("corrected_fact")]
    if not stale:
        return answer
    corrections = [{"wrong_claim": c["claim"], "verified_fact": c["corrected_fact"],
                    "source": c.get("best_source_url")} for c in stale]
    resp = llm.chat.completions.create(
        model=JUDGE_MODEL,
        temperature=0,
        max_tokens=600,
        messages=[
            {"role": "system", "content": f"Today is {date.today().isoformat()}. Rewrite the "
             "answer so it is correct. Change only what the verified corrections cover, use "
             "no other new facts, and keep it short. Reply with the rewritten answer only."},
            {"role": "user", "content": json.dumps(
                {"question": question, "answer": answer, "corrections": corrections})},
        ],
    )
    return resp.choices[0].message.content.strip()


@app.post("/ask")
def ask(req: AskRequest):
    try:
        answer = answer_question(req.question)
    except Exception as exc:  # e.g. Bifrost not running
        return {"error": f"Could not get an answer: {exc}"}
    check = run_check(answer, req.max_claims)
    try:
        fixed = fix_answer(req.question, answer, check["claims"])
    except Exception:
        fixed = None
    return {"question": req.question, "answer": answer, "check": check,
            "corrected_answer": fixed, "changed": bool(fixed) and fixed != answer}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/")
def home():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))
