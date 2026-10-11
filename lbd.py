from write import Lambda

r=Lambda().run_p

r(
    """
    t=x:y:x
    f=x:y:y
    if=b:x:y:b(x)(y)
    if(t)(a)(b)
    if(f)(a)(b)
    """
)

# ---------- 同样的东西用 Python 原生 lambda 实现一遍 ----------
# 守卫值(被选中的那个参数)用字符串 yes / no
t = lambda x: lambda y: x                       # λx.λy.x        真
f = lambda x: lambda y: y                       # λx.λy.y        假
iff = lambda b: lambda x: lambda y: b(x)(y)     # λb.λx.λy.b(x)(y)  条件

print(iff(t)("yes")("no"))   # yes
print(iff(f)("yes")("no"))   # no