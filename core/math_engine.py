import re

import sympy as sp


import sympy as sp
from sympy.parsing.sympy_parser import parse_expr, standard_transformations, implicit_multiplication_application

class MathEngine:
    def __init__(self, config):
        self.config = config
        self.x = sp.Symbol('x')
        self.k = sp.Symbol('k')
        # Combine standard rules with implicit multiplication rules (e.g., 2x becomes 2*x)
        self.transformations = standard_transformations + (implicit_multiplication_application,)

    def tokenize(self, expr_str):
        # Sort symbols by length descending to match longer symbols first
        symbols = sorted(list(self.config["tiles"].keys()) + ["="], key=len, reverse=True)
        tokens = []
        i = 0
        n = len(expr_str)
        while i < n:
            if expr_str[i] == ' ':
                i += 1
                continue
            matched = False
            for sym in symbols:
                if expr_str.startswith(sym, i):
                    tokens.append(sym)
                    i += len(sym)
                    matched = True
                    break
            if not matched:
                tokens.append(expr_str[i])
                i += 1
        return tokens

    def validate_sequence(self, tokens):
        """
        Validates a sequence of tokens against grammatical rules:
        - No leading or trailing binary operators (+, *, -). A leading "-" is allowed: it is a sign
          ("-sin(x)", "d/dx(-x)"), not a subtraction.
        - No consecutive binary operators (e.g. ++, +*).
        - No binary operator immediately following a boundary (d/dx(, int(), or preceding ).
        """
        if not tokens:
            return False, "Empty expression"

        BINARY_OPERATORS = {"+", "*", "-"}
        START_BOUNDARIES = {"d/dx(", "int("}

        # Split by "=" if present
        if "=" in tokens:
            # A chain like 4(1/x)x = 2+2 = 4 has several "=": every part must be a valid expression.
            parts, cur = [], []
            for t in tokens:
                if t == "=":
                    parts.append(cur)
                    cur = []
                else:
                    cur.append(t)
            parts.append(cur)
            for i, part in enumerate(parts):
                ok, msg = self.validate_sequence(part)
                if not ok:
                    label = "LHS" if i == 0 else "RHS" if i == len(parts) - 1 else f"Part {i + 1}"
                    return False, f"{label}: {msg}"
            return True, "Valid equation layout"

        # If no "=" is present, validate as a single expression
        # Rule 1: Cannot start with a binary operator
        if tokens[0] in BINARY_OPERATORS and tokens[0] != "-":
            return False, f"Expression starts with operator '{tokens[0]}'"

        # Rule 2: Cannot end with a binary operator
        if tokens[-1] in BINARY_OPERATORS:
            return False, f"Expression ends with operator '{tokens[-1]}'"

        for idx in range(len(tokens)):
            token = tokens[idx]

            # Rule 3: No consecutive binary operators
            if token in BINARY_OPERATORS and idx + 1 < len(tokens):
                next_token = tokens[idx + 1]
                if next_token in BINARY_OPERATORS:
                    return False, f"Consecutive operators '{token}' and '{next_token}'"

            # Rule 4: No operator immediately following d/dx( or int(
            if token in START_BOUNDARIES and idx + 1 < len(tokens):
                next_token = tokens[idx + 1]
                if next_token in BINARY_OPERATORS and next_token != "-":
                    return False, f"Operator '{next_token}' follows boundary '{token}'"

            # Rule 5: No operator immediately preceding )
            if token in BINARY_OPERATORS and idx + 1 < len(tokens):
                next_token = tokens[idx + 1]
                if next_token == ")":
                    return False, f"Operator '{token}' precedes ')'"

        return True, "Valid expression"

    def validate_equation(self, expr_str):
        if "=" not in expr_str:
            return False, "Missing equals sign (=)"

        # 1. Run tokenization and grammatical validation
        tokens = self.tokenize(expr_str)
        is_gram_valid, gram_msg = self.validate_sequence(tokens)
        if not is_gram_valid:
            return False, f"Invalid grammar: {gram_msg}"

        # An integral needs its constant of integration: "+C", or "-C" (the same family of antiderivatives).
        if "int(" in expr_str and self.config["require_plus_c"]:
            if not self._CONSTANT.search(expr_str.replace(" ", "")):
                return False, "Missing constant of integration (+C or -C)"

        try:
            # Every part of a chain (a = b = c) must equal the first one.
            # Strip the constant of integration, then compare.
            parts = [self._CONSTANT.sub("", p.replace(" ", "")) for p in expr_str.split("=")]
            first = self._parse_expression(parts[0])
            diffs = [self._parse_expression(part) - first for part in parts[1:]]
            if any(d.has(self.k) for d in diffs):
                return self._wildcard_k_works(diffs), "Valid"
            return all(sp.simplify(d) == 0 for d in diffs), "Valid"
            
        except Exception as e:
            return False, f"Syntax Error: {e}"

    @staticmethod
    def _wraps_whole(expr_str, open_idx):
        """True if the '(' at open_idx closes at the very last character."""
        depth = 0
        for i in range(open_idx, len(expr_str)):
            if expr_str[i] == "(":
                depth += 1
            elif expr_str[i] == ")":
                depth -= 1
                if depth == 0:
                    return i == len(expr_str) - 1
        return False

    # "+C" / "-C" as a term of its own: not "+Cx" (a product) or "+C(" (a call)
    _CONSTANT = re.compile(r"[+-]C(?![A-Za-z0-9(*^])")
    _FRACTION_TILE = re.compile(r"^\d/[\dx]$")
    _NUMBER_TILE = re.compile(r"^\d+$")
    # Tokens that are a value on their own (a term to multiply), as opposed to operators and openers.
    _VALUE_TILE = re.compile(r"^(?:\d+|\d/[\dx]|[xabkC]|\(x\+[ab]\)|x\*\*\d|e\^x|(?:sin|cos|ln)\(x\))$")

    def _join_tiles(self, expr_str):
        """
        Tiles are glued into one string, so neighbouring tiles can blur together: the tiles 2, 1/x, x spell
        "21/xx" (read as 21/(x*x) rather than 2*(1/x)*x), and x**4, 2 spell "x**42" (read as x to the 42nd).
        Re-tokenise and join the tiles explicitly: fractions get brackets, and two adjacent values are
        multiplied. The one exception is digit tiles, which still run together as a number (2, 3 -> 23).
        """
        out, prev = [], None
        for t in self.tokenize(expr_str):
            is_value = bool(self._VALUE_TILE.match(t)) or t in ("exp(",)
            prev_is_value = prev is not None and (bool(self._VALUE_TILE.match(prev)) or prev == ")")
            both_digits = prev is not None and self._NUMBER_TILE.match(prev) and self._NUMBER_TILE.match(t)
            if is_value and prev_is_value and not both_digits:
                out.append("*")
            out.append(f"({t})" if self._FRACTION_TILE.match(t) else t)
            prev = t
        return "".join(out)

    def _wildcard_k_works(self, diffs):
        """
        k is the "magic constant": a line containing k is valid if SOME single real number, the same for every k
        on the line, makes every link of the chain true. So 2k = 3 works (k = 3/2) and kx = 3x works (k = 3), but
        k = x does not (no constant equals x) and kx = 3 does not (k would have to be 3/x).
        """
        k = self.k
        target = next(d for d in diffs if d.has(k))
        try:
            candidates = sp.solve(target, k)
        except Exception:
            return False
        for cand in candidates:
            cand = sp.simplify(cand)
            if cand.free_symbols or cand.is_real is not True:   # must be a plain real number, not depend on x, a, b
                continue
            if all(sp.simplify(d.subs(k, cand)) == 0 for d in diffs):
                return True
        return False

    def _parse_expression(self, expr_str):
        return self._parse_grouped(self._join_tiles(expr_str))

    def _parse_grouped(self, expr_str):
        """
        Strips out custom calculus tile wrappers, parses the inner algebra 
        with implicit multiplication allowed, and applies the calculus operation.
        """
        # Normalize e**x and e^x to SymPy's exp(x)
        expr_str = expr_str.replace("e**x", "exp(x)").replace("e^x", "exp(x)")
        
        expr_str = expr_str.strip()
        for opener, operation in (("d/dx(", sp.diff), ("int(", sp.integrate)):
            if expr_str.startswith(opener) and self._wraps_whole(expr_str, len(opener) - 1):
                # The wrapper may itself contain another wrapper (d/dx(d/dx(x**3)) is a
                # second derivative), so evaluate the inside recursively.
                return operation(self._parse_grouped(expr_str[len(opener):-1]), self.x)

        # Standard algebraic expressions
        return parse_expr(expr_str, transformations=self.transformations)
