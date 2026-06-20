import sympy as sp


import sympy as sp
from sympy.parsing.sympy_parser import parse_expr, standard_transformations, implicit_multiplication_application

class MathEngine:
    def __init__(self, config):
        self.config = config
        self.x = sp.Symbol('x')
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
        - No leading or trailing binary operators (+, *).
        - No consecutive binary operators (e.g. ++, +*).
        - No binary operator immediately following a boundary (d/dx(, int(), or preceding ).
        """
        if not tokens:
            return False, "Empty expression"

        BINARY_OPERATORS = {"+", "*"}
        START_BOUNDARIES = {"d/dx(", "int("}

        # Split by "=" if present
        if "=" in tokens:
            eq_indices = [idx for idx, t in enumerate(tokens) if t == "="]
            if len(eq_indices) > 1:
                return False, "Multiple equals signs (=)"
            eq_idx = eq_indices[0]
            lhs = tokens[:eq_idx]
            rhs = tokens[eq_idx+1:]

            lhs_ok, lhs_msg = self.validate_sequence(lhs)
            if not lhs_ok:
                return False, f"LHS: {lhs_msg}"
            rhs_ok, rhs_msg = self.validate_sequence(rhs)
            if not rhs_ok:
                return False, f"RHS: {rhs_msg}"
            return True, "Valid equation layout"

        # If no "=" is present, validate as a single expression
        # Rule 1: Cannot start with a binary operator
        if tokens[0] in BINARY_OPERATORS:
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
                if next_token in BINARY_OPERATORS:
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

        left_str, right_str = expr_str.split("=", 1)

        # Enforce +C rule using CONFIG
        is_integration = "int(" in left_str or "int(" in right_str
        if is_integration and self.config["require_plus_c"]:
            if "+C" not in left_str.replace(" ", "") and "+C" not in right_str.replace(" ", ""):
                return False, "Missing constant of integration (+C)"

        try:
            # Clean up spacing and strip "+C" safely for evaluation
            eval_left = left_str.replace("+C", "").replace("+ C", "").strip()
            eval_right = right_str.replace("+C", "").replace("+ C", "").strip()
            
            # Parse using our flexible implicit parser
            left_expr = self._parse_expression(eval_left)
            right_expr = self._parse_expression(eval_right)
            
            is_valid = sp.simplify(left_expr - right_expr) == 0
            print(f"DEBUG: Comparing {left_expr} and {right_expr}, valid={is_valid}")
            return is_valid, "Valid"
            
        except Exception as e:
            return False, f"Syntax Error: {e}"

    def _parse_expression(self, expr_str):
        """
        Strips out custom calculus tile wrappers, parses the inner algebra 
        with implicit multiplication allowed, and applies the calculus operation.
        """
        # Normalize e**x and e^x to SymPy's exp(x)
        expr_str = expr_str.replace("e**x", "exp(x)").replace("e^x", "exp(x)")
        
        if expr_str.startswith("d/dx( ") or expr_str.startswith("d/dx("):
            # Extract everything between d/dx( and the closing )
            inner = expr_str[expr_str.index("(")+1 : -1]
            parsed_inner = parse_expr(inner, transformations=self.transformations)
            return sp.diff(parsed_inner, self.x)
            
        elif expr_str.startswith("int( ") or expr_str.startswith("int("):
            # Extract everything between int( and the closing )
            inner = expr_str[expr_str.index("(")+1 : -1]
            parsed_inner = parse_expr(inner, transformations=self.transformations)
            return sp.integrate(parsed_inner, self.x)
            
        else:
            # Standard algebraic expressions
            return parse_expr(expr_str, transformations=self.transformations)

