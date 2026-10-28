"""GDScript 结构自检：括号配对与缩进一致性。

只做能静态判定的粗检，用来在注入一段运行时脚本前避开低级错误。
"""

PAIRS = {")": "(", "]": "[", "}": "{"}
OPENERS = "([{"


def check(source):
    problems = []
    stack = []
    in_string = None
    lines = source.split("\n")

    for line_no, line in enumerate(lines, 1):
        i = 0
        while i < len(line):
            ch = line[i]
            if in_string:
                if ch == "\\":
                    i += 2
                    continue
                if ch == in_string:
                    in_string = None
                i += 1
                continue
            if ch == "#":
                break
            if ch in "\"'":
                in_string = ch
                i += 1
                continue
            if ch in OPENERS:
                stack.append((ch, line_no, i))
            elif ch in PAIRS:
                if not stack or stack[-1][0] != PAIRS[ch]:
                    problems.append("第 %d 行第 %d 列：多余的 %s" % (line_no, i, ch))
                else:
                    stack.pop()
            i += 1

    for ch, line_no, col in stack[-5:]:
        problems.append("第 %d 行第 %d 列：%s 未闭合" % (line_no, col, ch))
    if in_string:
        problems.append("文件结尾有未闭合的字符串")

    structure = []
    for line_no, line in enumerate(lines, 1):
        body = line.lstrip(" \t")
        if not body:
            continue
        lead = line[:len(line) - len(body)]
        if " " in lead:
            structure.append("第 %d 行使用空格缩进" % line_no)
    return problems, structure
