import json
import re
import unicodedata
from dataclasses import dataclass

from .closeai import API_KEY_PLACEHOLDER, safety_completion


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    category: str = "safe"
    message: str = ""


REJECTION_MESSAGES = {
    "violence": "Requests that could facilitate violence or physical harm cannot be executed.",
    "self_harm": "Requests that encourage or instruct self-harm cannot be executed.",
    "sexual_exploitation": "Sexual exploitation or abuse-related requests cannot be executed.",
    "hate_or_harassment": "Hateful, threatening, harassing, or targeted offensive requests cannot be executed.",
    "cyber_abuse": "Requests involving malware, credential theft, unauthorized access, or cyber abuse cannot be executed.",
    "illegal_activity": "Requests that could meaningfully facilitate illegal activity cannot be executed.",
    "privacy_abuse": "Requests that invade privacy, expose personal data, or enable surveillance abuse cannot be executed.",
    "safety_evasion": "Requests to bypass safeguards or conceal harmful activity cannot be executed.",
    "other_harm": "This request may be harmful or unsafe and cannot be executed.",
}
ALLOWED_CATEGORIES = {"safe", *REJECTION_MESSAGES}


LOCAL_RULES = (
    ("sexual_exploitation", re.compile(r"\b(?:child|minor|underage)\b.{0,60}\b(?:sexual|nude|porn|explicit|exploit|groom)\b|\b(?:sexual|nude|porn|explicit|groom)\b.{0,60}\b(?:child|minor|underage)\b")),
    ("safety_evasion", re.compile(r"\b(?:bypass|disable|evade|ignore|defeat)\b.{0,50}\b(?:safety|moderation|guardrail|content filter|security control)\b")),
    ("cyber_abuse", re.compile(r"\b(?:build|create|write|deploy|install|make)\b.{0,70}\b(?:ransomware|keylogger|credential stealer|phishing (?:page|site|kit)|botnet|malware|password stealer)\b|\b(?:steal|harvest|exfiltrate)\b.{0,50}\b(?:passwords?|credentials?|tokens?|session cookies?|private keys?)\b")),
    ("violence", re.compile(r"\b(?:how to|instructions? to|plan to|help me|best way to)\b.{0,80}\b(?:kill|murder|poison|bomb|shoot|stab|kidnap)\b|\b(?:build|make|assemble)\b.{0,50}\b(?:bomb|explosive device|lethal weapon)\b")),
    ("self_harm", re.compile(r"\b(?:how to|help me|encourage|convince|instructions? for)\b.{0,70}\b(?:suicide|kill myself|self[- ]harm)\b|\b(?:suicide|kill myself|self[- ]harm)\b.{0,70}\b(?:method|instructions?|without failing|encourage)\b")),
    ("hate_or_harassment", re.compile(r"\b(?:write|generate|create|send|post)\b.{0,60}\b(?:racial slurs?|hate speech|death threats?|harassing messages?|humiliat(?:e|ing))\b")),
    ("privacy_abuse", re.compile(r"\b(?:dox|doxx|stalk|track)\b.{0,60}\b(?:person|someone|victim|without (?:their )?consent|home address|location)\b|\b(?:steal|leak|publish)\b.{0,50}\b(?:medical records?|private messages?|personal data|home address)\b")),
    ("illegal_activity", re.compile(r"\b(?:launder money|forge (?:a |an )?(?:passport|identity|document)|evade law enforcement|sell illegal drugs|traffic (?:drugs|weapons|people)|hide criminal proceeds)\b")),
)

BENIGN_RESONA_REQUEST = re.compile(
    r"\b(?:add|adjust|build|change|create|design|edit|fix|improve|make|modify|move|remove|rename|replace|simplify|style|update)\b"
    r".{0,220}\b(?:account|ambient|animation|audio|button|captcha|card|color|dialog|display|email|font|form|frequency|home|icon|layout|login|mobile|mode|music|navigation|page|password|player|profile|register|responsive|settings|slider|sound|text|theme|ui|volume|website)\b"
)
BENIGN_RESONA_RISK_TERMS = re.compile(
    r"\b(?:attack|bomb|bypass|child|credential|dox|drug|evade|exploit|extort|forge|groom|hack|harass|hate|illegal|kill|malware|murder|nude|phish|poison|porn|ransomware|self[- ]harm|steal|suicide|surveillance|weapon)\b"
)
BENIGN_RESONA_EXPERIENCE = re.compile(
    r"\b(?:awake|calm|clean|comfortable|focus|healing|meditation|mobile|readable|relax|responsive|simple|sleep|soundscape)\b"
)


def _normalize(prompt):
    value = unicodedata.normalize("NFKC", prompt).casefold()
    return re.sub(r"\s+", " ", value).strip()


def _reject(category):
    return SafetyDecision(False, category, REJECTION_MESSAGES.get(category, REJECTION_MESSAGES["other_harm"]))


def review_agent_prompt(prompt, credential=API_KEY_PLACEHOLDER, timeout=45):
    normalized = _normalize(prompt)
    for category, pattern in LOCAL_RULES:
        if pattern.search(normalized):
            return _reject(category)

    # Ordinary interface and audio work is classified locally so a transient
    # provider issue cannot block clearly benign Resona changes. Anything with
    # risk language remains subject to the contextual provider review below.
    if (BENIGN_RESONA_REQUEST.search(normalized) or BENIGN_RESONA_EXPERIENCE.search(normalized)) and not BENIGN_RESONA_RISK_TERMS.search(normalized):
        return SafetyDecision(True)

    raw = safety_completion(prompt, credential, timeout)
    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
        result = json.loads(cleaned)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("The safety review returned an invalid response; the request was not executed") from exc
    if not isinstance(result, dict) or type(result.get("allowed")) is not bool:
        raise RuntimeError("The safety review returned an invalid decision; the request was not executed")
    category = str(result.get("category", "safe" if result["allowed"] else "other_harm"))
    if category not in ALLOWED_CATEGORIES:
        category = "safe" if result["allowed"] else "other_harm"
    return SafetyDecision(True) if result["allowed"] else _reject(category)
