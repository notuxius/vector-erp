"""Plan read-only assistant lookups before accessing either source."""
import re


CLARIFY = "Перевірити залишок чи знайти характеристики?"
DIRECT = {
    **dict.fromkeys(("залишки", "покажи залишки", "всі залишки", "усі залишки", "show stock", "stock"), "all"),
    **dict.fromkeys(("що є в наявності", "які товари є в наявності", "які деталі є в наявності",
                     "покажи наявність", "наявність", "what is in stock"), "in_stock"),
    "покажи вільні залишки": "free", "покажи деталі з виробничою потребою": "production",
    "деталі для виробництва": "production",
}
SKU = re.compile(r"(?=[\w./-]*[^\W\d_])(?=[\w./-]*\d)[\w./-]{3,80}|[A-Z]{2,}(?:-[A-Z]+)+")
PROCEDURE = re.compile(r"\b(?:як\s|що таке|чим відрізня|як[аиі] різниця|потрібно для|"
                       r"поясн|інструкц|процедур|приклад|чернетк|how (?:do|to)|explain)", re.I)
KNOWLEDGE = re.compile(r"\b(?:характеристик|параметр|цанг|оберт|напруг|охолоджен|гаранті|потужніст|"
                       r"номінальн|сумісн|відрізня|розмір|умов|документ|струм|інструкці|"
                       r"specs?\b|specification|warranty|voltage|cooling|collet|rpm|compatib)", re.I)
STOCK = re.compile(r"\b(?:залишк|залишок|наявн|на складі|на складах|зі складу|скільки|резерв|"
                   r"зарезерв|вільн|доступн|чи є|чи маєте|які є|залишил|закінчил|онови|кількіст|"
                   r"in stock|stock\b|inventory|available|how many|reserv)", re.I)
GENERIC_QUESTION = re.compile(r"^(?:а\s+)?(?:як\w*|що|чим|чому|коли|де|куди|поясни|порівняй|"
                              r"зроби|підготуй|what|how|why|explain|which)\b", re.I)
SEPARATOR = re.compile(r"\s+(?:і|й|та|а|and)\s+|[?;]\s*|[.,]\s+(?=(?:чи|як\w*|скільки|є|що)\b)", re.I)


def item_codes(text):
    words = (word.strip(".") for word in re.findall(r"[\w./-]+", text))
    return list(dict.fromkeys(word for word in words if SKU.fullmatch(word)
                and not re.fullmatch(r"\d+(?:\.\d+)?(?:квт|kw|вт|мм|mm|rpm|v|гц|hz)", word, re.I)))


def exact_item(term):
    return re.compile(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])", re.I)


def route_clause(clause):
    if PROCEDURE.search(clause):
        return "knowledge"
    if re.search(r"\b(?:на складі|на складах|у наявності|в наявності|in stock|залишок|залишки|вільно|зарезервовано)\b", clause, re.I):
        return "availability"
    # A component sold under a model number can itself be the item being searched.
    component = re.fullmatch(r"(чи є )?цанга\s+([\w./-]+)[?]?", clause, re.I)
    if component and SKU.fullmatch(component[2]):
        return "availability" if component[1] else None
    if KNOWLEDGE.search(clause):
        return "knowledge"
    if STOCK.search(clause):
        return "availability"
    if re.search(r"\b(?:номер\w*\s+накладн|видатков\w*\s+накладн|кнопк\w*\s+«)", clause, re.I):
        return "knowledge"
    return "knowledge" if GENERIC_QUESTION.search(clause) else None


def product_reference(clause):
    """Extract the shared subject, keeping the original product spelling and units."""
    shared = re.search(r"\(товар: (.+)\)$", clause, re.I)
    if shared:
        return shared[1].strip()
    before = re.split(r"\s+[-–—]\s+", clause, maxsplit=1)[0]
    if before != clause:
        return before.strip()
    name = re.sub(r"^(?:(?:чи )?є|чи маєте|скільки|покажи (?:залишки|наявність)|"
                  r"is there|do (?:you|we) have|how many)\s+", "", clause, flags=re.I)
    name = re.sub(r"^(?:на складі|[ув] наявності)\s+", "", name, flags=re.I)
    name = re.sub(r"(?:^|\s+)(?:є |вільн\w* )?(?:на складах|на складі|[ув] наявності|in stock)\b.*$", "", name, flags=re.I)
    if name and name != clause and not re.fullmatch(r"(?:він|вона|вони|воно|це|it|they)", name.strip(), re.I):
        return name.strip()
    owner = re.search(r"\b(?:для|моделі|у|в)\s+(.+)$", clause, re.I)
    if owner and not re.match(r"(?:нього|неї|них|наявності)\b", owner[1], re.I):
        return owner[1].strip(" ?!.,")
    return next(iter(item_codes(clause)), "")


def specification_subject(message):
    """Only enforce an exact match when the question supplies an item identity."""
    if not KNOWLEDGE.search(message) or PROCEDURE.search(message):
        return ""
    subject = product_reference(message)
    subject = re.sub(r"^навчання:\s*", "", subject, flags=re.I).strip(" ?!.")
    # Multiple models can be compared across different articles.
    if len(item_codes(message)) > 1:
        return ""
    model = next(iter(item_codes(subject)), "")
    return model or (subject if re.search(r"\s[-–—]\s|\(товар: ", message, re.I) else "")


def plan(message, previous_mode=None):
    message = message.strip()
    simple = message.casefold().rstrip("!? .,")

    def single(mode, **args):
        return [{"mode": mode, "message": message, **args}]

    if simple in DIRECT:
        return single("availability", availability_filters={"stock_state": DIRECT[simple]})
    literal = re.fullmatch(r"(?:залишки|stock)\s*:\s*(.*)", message, re.I | re.S)
    if literal or SKU.fullmatch(message):
        term = literal[1].strip() if literal else message
        return single("availability", availability_filters={"query": term, "stock_state": "all"})
    kb = re.match(r"^(?:база знань|знання|knowledge|kb)\s*[: ]\s*(.+)$", message, re.I | re.S)
    if kb:
        return [{"mode": "knowledge", "message": kb[1]}]
    if simple in ("оновити", "онови залишки", "refresh"):
        return single("availability")

    # Only separate a conjunction when it introduces an independent question.
    # Product names, decimal commas and lists of specifications stay together.
    # «Як перевірити, скільки …» asks for one procedure, not a second stock lookup.
    split_text = re.sub(r"\b(перевірити|дізнатися|подивитися|побачити|переглянути),\s+(?=скільки\b)",
                        r"\1 ", message, flags=re.I)
    clauses = [part.strip(" ,?!") for part in SEPARATOR.split(split_text) if part.strip(" ,?!")]
    modes = [route_clause(part) for part in clauses]
    if "knowledge" in modes and "availability" in modes:
        if PROCEDURE.search(clauses[0]) and not any(
            mode == "availability" and re.match(r"^(чи|скільки|що|які|покажи|is|how|show)\b", part, re.I)
            for mode, part in zip(modes, clauses)
        ):
            return single("knowledge")
        groups = {"availability": [], "knowledge": []}
        last = next(mode for mode in modes if mode)
        for mode, part in zip(modes, clauses):
            last = mode or last
            groups[last].append(part)
        stock, kb = (" і ".join(parts) for parts in groups.values())
        questions = {"availability": stock, "knowledge": kb}
        # Attach the shared item when the other question refers to it implicitly.
        for mode, question, other in (("knowledge", kb, stock), ("availability", stock, kb)):
            subject = product_reference(other)
            if subject and subject.casefold() not in question.casefold() and not product_reference(question):
                questions[mode] = f"{question} (товар: {subject})"
        return [{"mode": mode, "message": question} for mode, question in questions.items()]
    mode = route_clause(message)
    if previous_mode in ("availability", "knowledge") and (
        simple in ("коротше", "зроби коротше", "детальніше", "повтори") or
        (not mode and re.match(r"^а (?:там|на |в |у )", simple))
    ):
        return single(previous_mode)
    return single(mode) if mode else []
