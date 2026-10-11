"""λ 演算迷你语言：解析字符串 → 项 → 逐步 β 归约 → 展示过程与结果

支持三种语法：
    'x:y'       定义（λ 抽象，可链式 x:y:z 表示多参数）
    'n=x:y'     赋值（把项绑定到名字 n，不预先归约）
    'm=n(a)'    调用（应用，可链式 n(a)(b)，可原地(x:y)(a)）

标识符首字符可以是字母、下划线或数字（0、1 可作变量名，便于写丘奇数），
其后还可含 $ 与 '，因此内部新鲜名（x$1）与展示用去歧义名（y'）都能回灌。

类 Lambda 封装一个解释器实例：
    run(s)    执行脚本（多行，每行一条），不中途打印，返回整个归约过程与结果的字符串
    run_p(s)  同上，但直接把过程与结果打印出来（返回 None）
    env 在实例内跨调用持久（同名赋值会覆盖），想要干净环境就新建实例。

「结果」行：脚本中只要出现过调用语句，就打印最后一条调用语句的归约结果
（赋值语句本身不归约，末尾是赋值时也照样给出此前那次调用的结果）。

归约策略：最左最外（normal order），β + η。
    - 最外层是 redex ⇒ 立刻 β 收缩
    - 否则步进函数侧；函数侧已是范式且非 λ，再步进参数侧
    - λ 体内部仅在没有任何更外层可归约项时才进入
    - η：λv.(f v) 且 v 不在 f 中自由出现 ⇒ f。头部是 β-redex 时 β 优先，
      所以只有"孤立"的 λ 才会被 η 收掉（如 b:x:y:b(x)(y) ⇒ b:x:b(x)）

防死循环：
    - 循环检测：α 等价意义下重复出现的项 ⇒ 判定不收敛（如 Ω）
    - 步数上限：超过 max_steps 步仍未收敛 ⇒ 判定发散（如 Y 组合子无限展开）
发散时结果行显示 Diverged 标记（⟂ 发散…），不会死循环。
"""

import re

NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_$']*")
TOKEN = re.compile(NAME.pattern + r"|[():=]")
_FRESH = re.compile(r"^(.+?)\$\d+$")


# ---------- 项的表示（数据，而不是 Python 闭包） ----------
class Var:
    __match_args__ = ("n",)
    def __init__(self, n): self.n = n
    def __repr__(self): return self.n
    def __eq__(self, o): return isinstance(o, Var) and self.n == o.n
    def __hash__(self): return hash(("Var", self.n))


class Lam:
    __match_args__ = ("v", "b")
    def __init__(self, v, b): self.v, self.b = v, b
    def __repr__(self): return render(self)
    def __eq__(self, o):
        return isinstance(o, Lam) and self.v == o.v and self.b == o.b
    def __hash__(self): return hash(("Lam", self.v, self.b))


class App:
    __match_args__ = ("f", "a")
    def __init__(self, f, a): self.f, self.a = f, a
    def __repr__(self): return render(self)
    def __eq__(self, o):
        return isinstance(o, App) and self.f == o.f and self.a == o.a
    def __hash__(self): return hash(("App", self.f, self.a))


# ---------- 词法 + 递归下降解析 ----------
def lex(s):
    toks, i = [], 0
    while i < len(s):
        if s[i].isspace():
            i += 1
            continue
        m = TOKEN.match(s, i)
        if not m:
            raise SyntaxError(f"无法识别的字符: {s[i]!r}")
        toks.append(m.group())
        i = m.end()
    return toks


class Parser:
    def __init__(self, toks): self.toks, self.i = toks, 0

    def peek(self, k=0):
        return self.toks[self.i + k] if self.i + k < len(self.toks) else None

    def eat(self, t=None):
        tok = self.peek()
        if t is not None and tok != t:
            raise SyntaxError(f"期望 {t!r}，得到 {tok!r}")
        self.i += 1
        return tok

    def stmt(self):
        if NAME.fullmatch(self.peek() or "") and self.peek(1) == "=":
            name = self.eat()
            self.eat("=")
            return name, self.expr()
        return None, self.expr()

    def expr(self):
        if NAME.fullmatch(self.peek() or "") and self.peek(1) == ":":
            v = self.eat()
            self.eat(":")
            return Lam(v, self.expr())
        return self.app()

    def app(self):
        t = self.atom()
        while self.peek() == "(":
            self.eat("(")
            a = self.expr()
            self.eat(")")
            t = App(t, a)
        return t

    def atom(self):
        tok = self.eat()
        if tok == "(":
            t = self.expr()
            self.eat(")")
            return t
        if tok and NAME.fullmatch(tok):
            return Var(tok)
        raise SyntaxError(f"意外的符号或结尾: {tok!r}")


def parse(s):
    p = Parser(lex(s))
    name, term = p.stmt()
    if p.peek() is not None:
        raise SyntaxError(f"多余的符号: {p.peek()!r}")
    return name, term


# ---------- 辅助：自由变量 / α 等价键 ----------
def free(t, bound=frozenset()):
    match t:
        case Var(n): return set() if n in bound else {n}
        case Lam(v, b): return free(b, bound | {v})
        case App(f, a): return free(f, bound) | free(a, bound)


def short(t, limit=80):
    s = str(t)
    return s if len(s) <= limit else s[:limit] + "…"


def _prime(v, used):
    """给 v 追加撇号，直到不与 used 里的展示名冲突。"""
    n = v + "'"
    while n in used:
        n += "'"
    return n


def render(t, ren=None, used=None):
    """把项渲染成可直接回灌的字符串（Var/Lam/App 的 __repr__ 都走这里）。

    绑定名与**外层**绑定重名时（如 y:x:y:y），改用带撇号的展示名（y'），
    消除"最内层的 y 到底绑哪一层"的歧义；其余情况输出与旧版一致。

    ren:  绑定名 → 展示名
    used: 已占用的展示名（自由变量名 + 已用过的绑定展示名）
    """
    ren = {} if ren is None else ren
    used = set(free(t)) if used is None else used
    match t:
        case Var(n):
            return ren.get(n, n)
        case Lam(v, b):
            shown = _prime(v, used) if v in ren else v
            return f"{shown}:{render(b, {**ren, v: shown}, used | {shown})}"
        case App(f, a):
            fs = render(f, ren, used)
            if isinstance(f, Lam):
                fs = f"({fs})"
            return f"{fs}({render(a, ren, used)})"


def alpha_key(t, d=None, fresh_map=None):
    """α 等价键：绑定变量统一换成序号，自由变量保留名字。

    形如 name$N 的新鲜名按「基名 + 首次出现顺序」归一化：同一项在不同步骤
    被编成 x$1 / x$3 时仍判为等价（提高循环检测命中率），而基名不同的
    x$1 / y$1 不会再被错判为同一个自由变量。
    """
    d = {} if d is None else d
    fresh_map = {} if fresh_map is None else fresh_map
    match t:
        case Var(n):
            if n in d:
                return ("v", d[n])
            m = _FRESH.match(n)
            if m:
                base = m.group(1)
                if (base, n) not in fresh_map:
                    fresh_map[(base, n)] = len(fresh_map)
                return ("f", base, fresh_map[(base, n)])
            return ("v", n)
        case Lam(v, b):
            return ("L", alpha_key(b, {**d, v: len(d)}, fresh_map))
        case App(f, a):
            return ("A", alpha_key(f, d, fresh_map),
                         alpha_key(a, d, fresh_map))


# ---------- 结构打印 ----------
def show(t, p=0):
    pad = "  " * p
    match t:
        case Var(n):
            print(f"{pad}Var({n})")
        case Lam(v, b):
            print(f"{pad}Lam(λ{v}.")
            show(b, p + 1)
            print(f"{pad})")
        case App(f, a):
            print(f"{pad}App(")
            show(f, p + 1)
            show(a, p + 1)
            print(f"{pad})")


# ---------- 发散标记 ----------
class Diverged:
    def __init__(self, term, reason):
        self.term, self.reason = term, reason

    def __repr__(self):
        why = "检测到循环归约" if self.reason == "cycle" else "超过步数上限"
        return f"⟂ 发散({why}): {short(self.term)}"

    __str__ = __repr__


# ---------- 解释器 ----------
class Lambda:
    def __init__(self, max_steps=100):
        self.env = {}
        self.max_steps = max_steps
        self._counter = 0

    # ---- α 改名与替换 ----
    def fresh(self, v):
        self._counter += 1
        return f"{v}${self._counter}"

    def subst(self, t, x, s):
        """把 t 中自由的 x 替换成 s；会捕获自由变量的 λ 先做 α 改名。

        free(s) 只在入口算一次（旧版对每个 Lam 节点都重算一遍）。
        """
        fs = free(s)

        def go(t):
            match t:
                case Var(n):
                    return s if n == x else t
                case App(f, a):
                    return App(go(f), go(a))
                case Lam(v, b):
                    if v == x:
                        return t
                    if v in fs:
                        nv = self.fresh(v)
                        # 必须先改名、后替换：否则 s 里自由出现的 v 会被一起改掉
                        return Lam(nv, self.subst(self.subst(b, v, Var(nv)), x, s))
                    return Lam(v, go(b))

        return go(t)

    # ---- 单步归约（normal order + η） ----
    def step(self, t, bound=frozenset()):
        """执行一步归约（env 名字展开、β 收缩或 η 收缩），返回新项；None 表示已是范式。

        关键：App 的最外层若是 redex，优先 β；否则才步进函数侧。
        这样才符合"最左最外"，不会卡在 λ 体内部的 redex 上。
        η 只在"孤立"的 λ（λv.f v 且 v ∉ FV(f)）上生效——若它正被应用，
        头部的 β 会先一步走掉。
        """
        match t:
            case Var(n):
                if n in self.env and n not in bound:
                    return self.env[n]
                return None
            case Lam(v, App(f, Var(w))) if w == v and v not in free(f):
                return f
            case Lam(v, b):
                nb = self.step(b, bound | {v})
                return Lam(v, nb) if nb is not None else None
            case App(f, a):
                # 最外层是 redex ⇒ 立刻 β
                if isinstance(f, Lam):
                    return self.subst(f.b, f.v, a)
                # 否则步进函数侧
                nf = self.step(f, bound)
                if nf is not None:
                    return App(nf, a)
                # 函数侧已是范式且非 λ，再步进参数侧
                na = self.step(a, bound)
                if na is not None:
                    return App(f, na)
                return None

    # ---- 归约到范式或发散 ----
    def reduce(self, t):
        """逐步归约，返回 (步骤列表, 状态)。状态: nf | cycle | limit

        步骤列表含初始项，故归约步数 = len(steps) - 1，上限为 max_steps。
        """
        if isinstance(t, str):
            t = parse(t)[1]
        steps = [t]
        seen = {alpha_key(t)}
        while len(steps) - 1 < self.max_steps:
            nxt = self.step(steps[-1])
            if nxt is None:
                return steps, "nf"
            k = alpha_key(nxt)
            if k in seen:
                steps.append(nxt)
                return steps, "cycle"
            seen.add(k)
            steps.append(nxt)
        return steps, "limit"

    # ---- 过程展示 ----
    def _format_steps(self, src, steps, st):
        out = [f"{src}:"]
        if len(steps) <= 12:
            for i, t in enumerate(steps):
                out.append(f"  {'⇒' if i else '·'} {short(t)}")
        else:
            for i, t in enumerate(steps[:5]):
                out.append(f"  {'⇒' if i else '·'} {short(t)}")
            out.append(f"  … 共 {len(steps) - 1} 步 …")
            for t in steps[-3:]:
                out.append(f"  ⇒ {short(t)}")
        if st == "cycle":
            out.append("  ⚠ 检测到循环归约，不收敛")
        elif st == "limit":
            out.append(f"  ⚠ 超过 {self.max_steps} 步仍未收敛，判定为发散")
        return out

    # ---- 语句执行 ----
    def _exec(self, s):
        out, last, called = [], None, False
        for line in s.splitlines():
            line = line.strip()
            if not line:
                continue
            name, term = parse(line)
            if name is not None:
                self.env[name] = term
                out.append(f"{name} := {term}")
            else:
                steps, st = self.reduce(term)
                out.extend(self._format_steps(line, steps, st))
                last = steps[-1] if st == "nf" else Diverged(steps[-1], st)
                called = True
        if called:
            out.append(f"结果: {short(last)}")
        return "\n".join(out)

    def run(self, s):
        return self._exec(s)

    def run_p(self, s):
        print(self._exec(s))


if __name__ == "__main__":
    lc = Lambda()

    lc.run_p("x:y")
    lc.run_p("n=x:y")
    lc.run_p("m=n(a)")
    lc.run_p("m")

    print("-" * 40)

    lc.run_p("""
        true=x:y:x
        false=x:y:y
        if2=b:x:y:b(x)(y)
        res=if2(true)(false)
    """)
    lc.run_p("res")
    lc.run_p("if2(true)(yes)(no)")

    print("-" * 40)
    print("作用域测试：")
    lc.run("x=a")
    lc.run("k=x:y:x")
    lc.run_p("k")

    print("-" * 40)
    print("发散检测（不会死循环）：")
    lc.run("omega=(x:x(x))(x:x(x))")
    lc.run_p("omega")
    lc.run("Y=f:(x:f(x(x)))(x:f(x(x)))")
    lc.run_p("Y(Y)")

    print("-" * 40)
    print("normal order 回归测试（原版会卡在 λ 体内）：")
    lc.run("omega2=(x:x(x))(x:x(x))")
    lc.run_p("(x:y:omega2)(a)")    # 应第一步就 β 成 y:omega2，然后发散
    lc.run_p("(x:y:(x)(omega2))(z)")

    print("-" * 40)
    print("η 归约（λv.f v 且 v∉FV(f) ⇒ f）：")
    lc.run_p("x:y:h(x)(y)")    # 内层先 η ⇒ x:h(x)，外层再 η ⇒ h
    lc.run_p("x:x(y)")         # 参数不是绑定变量本身 ⇒ 不 η，λ 原样保留（已是范式）

    print("-" * 40)
    print("数字变量名（丘奇数，0/1 可直接当名字）：")
    lc.run_p("""
        0=f:x:x
        1=f:x:f(x)
        succ=n:f:x:f(n(f)(x))
    """)
    lc.run_p("1")           # 孤立的 1 会被 η 收成 f:f
    lc.run_p("succ(1)")     # ⇒ 2，即 f:x:f(f(x))

    print("-" * 40)
    print("结构打印（AST）：")
    show(parse("x:y:x")[1])

    print("-" * 40)
    text = lc.run("m")
    lines = text.splitlines()
    print(f"run 返回 {len(lines)} 行字符串，首行: {lines[0]}，末行: {lines[-1]}")