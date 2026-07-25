# 贡献指南

欢迎贡献代码！请遵循以下规范。

## 提交代码

### 分支管理

- `main`: 稳定版本分支
- `dev`: 开发分支，所有 PR 应提交到这里

### 提交信息格式

```
类型: 简短描述

详细描述（可选）

修复: #issue_number（如果适用）
```

类型说明：
- `feat`: 新功能
- `fix`: 修复 Bug
- `docs`: 文档更新
- `refactor`: 重构
- `perf`: 性能优化
- `test`: 测试
- `chore`: 其他杂项

### PR 要求

1. 代码通过所有测试
2. 提交信息清晰
3. 提供必要的文档更新

## 代码规范

### Python

- 使用 Python 3.12+
- 遵循 PEP 8 规范
- 使用 `typing` 进行类型注解
- 函数/方法应添加文档字符串

### 代码风格

- 变量命名：`snake_case`
- 类命名：`PascalCase`
- 常量命名：`UPPER_CASE`
- 每行不超过 120 字符

## 开发流程

1. Fork 仓库
2. 创建功能分支：`git checkout -b feature/your-feature`
3. 提交代码
4. 创建 PR 到 `dev` 分支

## 测试

```bash
# 运行测试
python -m pytest
```

## 报告问题

请在 Issues 中报告问题，包含以下信息：

- 复现步骤
- 预期结果
- 实际结果
- 环境信息（Python 版本、操作系统）

## 许可证

所有贡献代码均遵循 MIT 许可证。