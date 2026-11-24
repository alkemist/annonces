import re
import unicodedata
from typing import Literal, TypedDict


Subject = Literal["free", "certificat"]
MessageType = Literal["question", "answer", "politeness"]


class ConversationMessage(TypedDict):
    sender: Literal["me", "author"]
    content: str
    order: int
    type: MessageType
    subjects: list[Subject]


def _normalize(text: str) -> str:
    text = text.lower().replace("’", "'").replace("‘", "'")
    return "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


def _subjects(text: str) -> list[Subject]:
    subjects: list[Subject] = []
    if re.search(r"\b(?:gratuit(?:e|s|es)?|payant(?:e|s|es)?)\b", text):
        subjects.append("free")
    if re.search(r"\bcertificats?\b|\battestation(?:s)? d[' ]engagement\b", text):
        subjects.append("certificat")
    return subjects


def _is_question(text: str) -> bool:
    return bool(re.search(
        r"\?|(?:\best[- ]ce (?:que|qu')|\b(?:avez|fournissez|proposez|est|sont)[- ](?:vous|il|elle|ils|elles)\b)",
        text,
    ))


def _explicit_value(text: str, subject: Subject) -> bool | None:
    """Interprète uniquement les formulations explicites, sans deviner les cas ambigus."""
    if re.search(
        r"\b(?:peut[- ]etre|probablement|apparemment|je (?:crois|pense|suppose)|"
        r"(?:je\s+)?(?:ne\s+)?sais\s+pas|aucune idee|si)\b",
        text,
    ):
        return None
    if subject == "free":
        negative = re.search(
            r"\b(?:pas|non|plus|jamais)\s+(?:du tout\s+)?gratuit(?:e|s|es)?\b"
            r"|\b(?:c[' ]est|il est|elle est|c[' ]etait|il sera|elle sera)\s+payant(?:e|s|es)?\b",
            text,
        )
        positive = re.search(
            r"\b(?:c[' ]est|il est|elle est|c[' ]etait|il sera|elle sera)\s+gratuit(?:e|s|es)?\b"
            r"|\bpas\s+payant(?:e|s|es)?\b",
            text,
        )
    else:
        item = r"(?:certificats?|attestations?(?: d[' ]engagement)?)"
        negative = re.search(
            rf"\b(?:pas|aucun|aucune|sans|ni)\s+(?:de\s+|d[' ]|un\s+|une\s+)?{item}\b"
            rf"|\b{item}\s+(?:n[' ](?:est|a)|(?:est|a))\s+pas\b",
            text,
        )
        positive = re.search(
            rf"\b(?:j[' ]ai|je (?:possede|fournis|donne|delivre)|avec|voici)\s+"
            rf"(?:bien\s+)?(?:un\s+|une\s+|le\s+|la\s+|mon\s+|son\s+)?{item}\b",
            text,
        )
        if re.search(r"\b(?:ne|n[' ])\s*(?:possede|fournis|donne|delivre)\s+pas\b", text):
            positive = None

    if bool(positive) == bool(negative):
        return None
    return bool(positive)


def analyze_messages(messages: list[ConversationMessage]) -> tuple[bool | None, bool | None]:
    values: dict[Subject, bool | None] = {"free": None, "certificat": None}
    pending: set[Subject] = set()
    asked: set[Subject] = set()
    previous_sender: str | None = None

    for message in messages:
        text = _normalize(message["content"])
        subjects = _subjects(text)
        question = _is_question(text)
        message["subjects"] = subjects

        if question:
            message["type"] = "question"
            if message["sender"] == "me":
                if previous_sender != "me":
                    pending.clear()
                    asked.clear()
                pending.update(subjects)
                asked.update(subjects)
            else:
                pending.clear()
                asked.clear()
        elif message["sender"] == "author" and asked:
            clauses = re.split(r"\s+(?:et|mais|par contre)\s+|[.!;\n]+", text)
            resolved: set[Subject] = set()
            answered = False
            inferred: set[Subject] = set()
            for clause in clauses:
                short_answer = re.fullmatch(r"\s*(?:(?:bonjour|bonsoir)\s*,?\s*)?(oui|si|non)\s*,?\s*", clause)
                clause_subjects = set(_subjects(clause)) & asked
                if short_answer:
                    answered = True
                implicit_subjects = pending if pending else asked
                if not clause_subjects and len(implicit_subjects) == 1 and not subjects and short_answer:
                    clause_subjects = set(implicit_subjects)
                if clause_subjects:
                    answered = True
                for subject in clause_subjects:
                    value = _explicit_value(clause, subject)
                    if value is None and short_answer and not _subjects(clause):
                        value = short_answer.group(1) != "non"
                    if value is not None:
                        values[subject] = value
                        resolved.add(subject)
                        inferred.add(subject)
            if not subjects:
                message["subjects"] = [subject for subject in ("free", "certificat") if subject in inferred]
            message["type"] = "answer" if answered else "politeness"
            pending.difference_update(resolved)
        else:
            message["type"] = "politeness"
            if message["sender"] == "me":
                pending.clear()
                asked.clear()
        previous_sender = message["sender"]

    return values["free"], values["certificat"]


def answer_status(messages: list[ConversationMessage]) -> tuple[bool | None, bool | None]:
    # need_answer, wait_answer
    if not messages:
        return None, None

    last_message = messages[-1]
    is_question = last_message["type"] == "question"
    if last_message["sender"] == "author":
        return is_question, None
    return None, is_question
