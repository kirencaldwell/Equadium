import re

import sympy as sp


import sympy as sp
from sympy.parsing.sympy_parser import parse_expr, standard_transformations, implicit_multiplication_application

class TrivialZeroError(ValueError):
    """A group of tiles that works out to 0 (d/dx of a constant, x-x...). Zero times anything is 0, so it would let a
    play pad a line with high-value tiles that do no work."""


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
        START_BOUNDARIES = {"d/dx(", "int(", "exp(", "("}
        OPENERS = {"d/dx(", "int(", "exp(", "("}

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

        # Brackets must balance, and a group can't be empty: "(x+1)" and "d/dx(x)" yes, "(x" and "()" no.
        depth = 0
        for idx, token in enumerate(tokens):
            if token in OPENERS:
                depth += 1
                if idx + 1 < len(tokens) and tokens[idx + 1] == ")":
                    return False, "Empty brackets"
            elif token == ")":
                depth -= 1
                if depth < 0:
                    return False, "A ')' has no matching opening bracket"
        if depth != 0:
            return False, "A bracket is never closed"

        for idx in range(len(tokens)):
            token = tokens[idx]

            # Rule 3: No consecutive binary operators
            if token in BINARY_OPERATORS and idx + 1 < len(tokens):
                next_token = tokens[idx + 1]
                if next_token in BINARY_OPERATORS:
                    return False, f"Consecutive operators '{token}' and '{next_token}'"

            # Rule 4: No operator immediately following d/dx(, int(, exp( or (
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

        parts, cur = [], []
        for t in tokens:
            if t == "=":
                parts.append(cur)
                cur = []
            else:
                cur.append(t)
        parts.append(cur)

        # The "+C" / "-C" tiles are the constant of integration. Take them out; an integral needs one.
        found = False
        stripped = []
        for part in parts:
            part, has_constant = self._strip_constant_terms(part)
            found = found or has_constant
            stripped.append(part)
        # A C tile anywhere in the line satisfies the requirement; the maths still has to hold (a C inside a product
        # such as "Cx" is an ordinary symbol, so it can't smuggle anything past the check).
        if "int(" in tokens and self.config["require_plus_c"] and not found and "C" not in tokens:
            return False, "Missing constant of integration (+C or -C)"

        try:
            # Every part of a chain (a = b = c) must equal the first one. A line may also hold free constants: k, and
            # one for every integral (see _constants_work), so it is true if SOME choice of them makes every link true.
            self._consts, self._collecting = [], True
            try:
                first = self._parse_token_list(stripped[0])
                self._reject_zero(first, "a side of the equation")
                diffs = []
                for part in stripped[1:]:
                    value = self._parse_token_list(part)
                    self._reject_zero(value, "a side of the equation")
                    diffs.append(value - first)
                consts = list(self._consts)
            finally:
                self._collecting = False
            if any(d.has(self.k) for d in diffs):
                consts.append(self.k)
            return self._constants_work(diffs, consts), "Valid"

        except TrivialZeroError as e:
            return False, str(e)
        except Exception as e:
            return False, f"Syntax Error: {e}"

    @staticmethod
    def _strip_constant_terms(tokens):
        """
        Removes the constant of integration when it is a term of its own, wherever it sits: "+C", "-C", or a bare
        leading "C" (at the start of a side or of a bracket, as in "C-x"). Not "+Cx", a product. Returns (tokens, found).
        """
        out, found, i = [], False, 0
        while i < len(tokens):
            ends_term = lambda j: j >= len(tokens) or tokens[j] in ("+", "-", ")")
            if tokens[i] in ("+", "-") and i + 1 < len(tokens) and tokens[i + 1] == "C" and ends_term(i + 2):
                found = True
                i += 2
                continue
            if tokens[i] == "C" and (i == 0 or tokens[i - 1] == "(") and ends_term(i + 1):
                found = True
                i += 1
                continue
            out.append(tokens[i])
            i += 1
        return out, found

    # "+C" / "-C" as a term of its own: not "+Cx" (a product) or "+C(" (a call)
    _CONSTANT = re.compile(r"[+-]C(?![A-Za-z0-9(*^])")
    _FRACTION_TILE = re.compile(r"^\d/[\dx]$")
    # Tokens that are a value on their own (a term to multiply), as opposed to operators and openers.
    _VALUE_TILE = re.compile(r"^(?:Zq\d+|\d+|\d/[\dx]|[xabkCi]|\(x\+[ab]\)|x\*\*\d|e\^x|(?:sin|cos|ln)\(x\))$")

    def _join_tiles(self, expr_str):
        return self._join_token_list(self.tokenize(expr_str))

    def _join_token_list(self, tokens):
        """
        Tiles are glued into one string, so neighbouring tiles can blur together: the tiles 2, 1/x, x spell
        "21/xx" (read as 21/(x*x) rather than 2*(1/x)*x), and x**4, 2 spell "x**42" (read as x to the 42nd).
        Join the tiles explicitly: fractions get brackets, and two adjacent values are multiplied, digit
        tiles included (the tiles 2, 2 are 2*2 = 4, not twenty-two).
        """
        out, prev = [], None
        for t in tokens:
            is_value = bool(self._VALUE_TILE.match(t)) or t in ("exp(", "(")     # starts a term of its own
            prev_is_value = prev is not None and (bool(self._VALUE_TILE.match(prev)) or prev == ")")
            if is_value and prev_is_value:
                out.append("*")
            out.append(f"({t})" if self._FRACTION_TILE.match(t) else t)
            prev = t
        return "".join(out)

    # Sample points for (x, a, b) used to find the values of a line's free constants (see _constants_work)
    _SAMPLES = [(sp.Rational(1, 3), sp.Rational(2, 5), sp.Rational(3, 7)), (sp.Rational(2, 3), sp.Rational(5, 4), sp.Rational(1, 6)),
                (sp.Rational(5, 7), sp.Rational(3, 2), sp.Rational(7, 5)), (sp.Rational(3, 2), sp.Rational(1, 7), sp.Rational(4, 3)),
                (sp.Rational(7, 4), sp.Rational(5, 6), sp.Rational(2, 9)), (sp.Rational(9, 5), sp.Rational(4, 7), sp.Rational(5, 8))]

    def _is_zero(self, d):
        """
        Is the expression d zero for every x, a and b? SymPy's simplify settles most things, but it can miss identities
        that involve complex numbers (exp(ix) = cos(x) + i sin(x)), so when it is not sure the expression is also
        rewritten in terms of exp and, failing that, compared numerically at several points.
        """
        if sp.simplify(d) == 0:
            return True
        if not d.has(sp.I):
            return False
        try:
            if sp.simplify(d.rewrite(sp.exp)) == 0:
                return True
        except Exception:
            pass
        x, a, b = self.x, sp.Symbol("a"), sp.Symbol("b")
        try:
            for px, pa, pb in self._SAMPLES:
                value = complex(sp.N(d.subs({x: px, a: pa, b: pb}), 30))
                if not abs(value) < 1e-9:                                  # also false for nan
                    return False
            return True
        except Exception:
            return False

    def _constants_work(self, diffs, consts):
        """
        Is there a choice of real values for the free constants `consts` that makes every difference in `diffs`
        zero for all x, a and b? The free constants are k (the wild constant: 2k = 3 works; it may be any real number
        except 0) and the constant of
        integration of every integral on the line (so sin(x) * int(x**2) can equal x**3 sin(x)/3, with the
        constant chosen as 0, or int(x) = x**2/2 + 7).

        With no constants this is just "every difference simplifies to zero". Otherwise the constants are
        found by solving at a handful of sample points, and the result is then checked symbolically, so a
        constant that would have to depend on x (k = x, kx = 3) is rejected.
        """
        consts = [c for c in dict.fromkeys(consts) if any(d.has(c) for d in diffs)]
        if not consts:
            return all(self._is_zero(d) for d in diffs)
        x, a, b = self.x, sp.Symbol("a"), sp.Symbol("b")
        equations = [d.subs({x: px, a: pa, b: pb}) for d in diffs for px, pa, pb in self._SAMPLES]
        try:
            solutions = sp.solve(equations, consts, dict=True)
        except Exception:
            return False
        for sol in solutions:
            # A constant the equations leave free (or only tie to another one) is filled in with 0, but k is
            # never allowed to be 0: that would make every line of the form k(...) = k(...) true for free.
            for fill in (0, 1):
                default = {c: (1 if c == self.k else fill) for c in consts}
                values = {c: sp.sympify(sol.get(c, default[c])).subs(default) for c in consts}
                if any(v.free_symbols or v.is_real is not True for v in values.values()):
                    continue                                                  # a constant must be a plain real number
                if self.k in values and values[self.k] == 0:
                    continue
                if all(self._is_zero(d.subs(values)) for d in diffs):
                    return True
        return False

    def _parse_expression(self, expr_str):
        if not getattr(self, "_collecting", False):
            self._consts = []
        return self._parse_token_list(self.tokenize(expr_str))

    _OPENERS = ("d/dx(", "int(", "exp(", "(")

    def _closing_index(self, tokens, start):
        """Index of the ")" that closes the opener at tokens[start]."""
        depth = 0
        for i in range(start, len(tokens)):
            if tokens[i] in self._OPENERS:
                depth += 1
            elif tokens[i] == ")":
                depth -= 1
                if depth == 0:
                    return i
        raise ValueError(f"'{tokens[start]}' is never closed")

    @staticmethod
    def _reject_zero(value, what):
        if value == 0:
            raise TrivialZeroError(f"{what} works out to 0, which can't be used to pad out an equation")

    def _parse_token_list(self, tokens):
        """
        Parses a run of tiles. A d/dx( ) or int( ) group may sit anywhere in it, not only around a whole side:
        each group is worked out on its own (groups nest: d/dx(d/dx(x**3)) is a second derivative) and stands in
        for the number or expression it produces, so d/dx(x**2) + x and sin(x) * int(x**2) both make sense.
        An integral comes with a fresh constant of integration (see _constants_work).
        """
        out, env, i = [], {}, 0
        while i < len(tokens):
            t = tokens[i]
            if t in ("d/dx(", "int("):
                j = self._closing_index(tokens, i)
                inner = self._parse_token_list(tokens[i + 1:j])
                if t == "d/dx(":
                    value = sp.diff(inner, self.x)
                    self._reject_zero(value, "A derivative")
                else:
                    c = sp.Symbol(f"_C{len(self._consts)}")
                    self._consts.append(c)
                    value = sp.integrate(inner, self.x) + c
                name = f"Zq{len(env)}"
                env[name] = value
                out.append(name)
                i = j + 1
            else:
                if t in ("(", "exp("):                         # a bracket that is 0 would zero out whatever it multiplies
                    j = self._closing_index(tokens, i)
                    n = len(self._consts)
                    self._reject_zero(self._parse_token_list(tokens[i + 1:j]), "A bracket")
                    del self._consts[n:]
                out.append(t)
                i += 1
        text = self._join_token_list(out).replace("e**x", "exp(x)").replace("e^x", "exp(x)")
        return parse_expr(text, local_dict={**env, "i": sp.I}, transformations=self.transformations)
