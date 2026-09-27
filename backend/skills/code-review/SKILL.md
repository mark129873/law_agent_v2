---
name: code-review
description: 执行全面的代码审查，包括安全性、性能和可维护性分析。当用户要求审查代码、检查 Bug 或审计代码库时使用。
---

# 代码审查技能

你现在具备执行全面代码审查的专业能力。请遵循以下结构化方法：

## 审查清单

### 1. 安全性（关键）

检查以下内容：
- [ ] **注入漏洞**：SQL 注入、命令注入、XSS、模板注入
- [ ] **身份认证问题**：硬编码凭据、弱认证机制
- [ ] **授权缺陷**：缺失访问控制、IDOR（不安全的直接对象引用）
- [ ] **数据泄露**：日志、错误消息中包含敏感数据
- [ ] **密码学问题**：弱算法、不当的密钥管理
- [ ] **依赖项**：已知漏洞（使用 `npm audit`、`pip-audit` 检查）

```bash
# 快速安全扫描
npm audit                    # Node.js
pip-audit                    # Python
cargo audit                  # Rust
grep -r "password\|secret\|api_key" --include="*.py" --include="*.js"
```

### 2. 正确性

检查以下内容：
- [ ] **逻辑错误**：差一错误（off-by-one）、空值处理、边界情况
- [ ] **竞态条件**：并发访问时缺少同步机制
- [ ] **资源泄漏**：文件、连接未关闭，内存泄漏
- [ ] **错误处理**：异常被吞掉、缺少错误处理路径
- [ ] **类型安全**：隐式类型转换、`any` 类型

### 3. 性能

检查以下内容：
- [ ] **N+1 查询**：在循环中执行数据库调用
- [ ] **内存问题**：大量内存分配、引用被长期保留
- [ ] **阻塞操作**：在异步代码中执行同步 I/O
- [ ] **低效算法**：可以使用 O(n) 却使用了 O(n²)
- [ ] **缺少缓存**：重复执行高成本计算

### 4. 可维护性

检查以下内容：
- [ ] **命名**：清晰、一致、具有描述性
- [ ] **复杂度**：函数超过 50 行、嵌套深度超过 3 层
- [ ] **重复代码**：复制粘贴的代码块
- [ ] **无用代码**：未使用的导入、不可到达的分支
- [ ] **注释**：过时、冗余，或在需要的位置缺少注释

### 5. 测试

检查以下内容：
- [ ] **覆盖率**：关键路径是否有测试覆盖
- [ ] **边界情况**：空值、空内容、边界值
- [ ] **Mock**：外部依赖是否被隔离
- [ ] **断言**：检查是否具体且有实际意义

## 审查输出格式

```markdown
## 代码审查：[文件/组件名称]

### 总结
[1-2 句话概述]

### 严重问题
1. **[问题]**（第 X 行）：[描述]
   - 影响：[可能出现什么问题]
   - 修复：[建议的解决方案]

### 改进建议
1. **[建议]**（第 X 行）：[描述]

### 值得肯定的地方
- [做得好的部分]

### 结论
[ ] 可以合并
[ ] 需要少量修改
[ ] 需要重大修改
```

## 常见需要标记的问题模式

### Python

```python
# 错误：SQL 注入
cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")
# 正确：
cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))

# 错误：命令注入
os.system(f"ls {user_input}")
# 正确：
subprocess.run(["ls", user_input], check=True)

# 错误：可变默认参数
def append(item, lst=[]):  # Bug：共享可变默认值
# 正确：
def append(item, lst=None):
    lst = lst or []
```

### JavaScript/TypeScript

```javascript
// 错误：原型污染
Object.assign(target, userInput)
// 正确：
Object.assign(target, sanitize(userInput))

// 错误：使用 eval
eval(userCode)
// 正确：绝不要对用户输入使用 eval

// 错误：回调地狱
getData(x => process(x, y => save(y, z => done(z))))
// 正确：
const data = await getData();
const processed = await process(data);
await save(processed);
```

## 审查命令

```bash
# 查看最近的变更
git diff HEAD~5 --stat
git log --oneline -10

# 查找潜在问题
grep -rn "TODO\|FIXME\|HACK\|XXX" .
grep -rn "password\|secret\|token" . --include="*.py"

# 检查复杂度（Python）
pip install radon && radon cc . -a

# 检查依赖项
npm outdated  # Node
pip list --outdated  # Python
```

## 审查工作流

1. **理解上下文**：阅读 PR 描述和关联的 Issue
2. **运行代码**：如果可能，在本地进行构建、测试和运行
3. **自顶向下阅读**：从主要入口点开始
4. **检查测试**：变更是否有测试覆盖？测试是否通过？
5. **安全扫描**：运行自动化安全工具
6. **人工审查**：使用上述检查清单进行审查
7. **撰写反馈**：反馈要具体，给出修复建议，并保持友善