# Skill: 实现/改码

来源改造：opencode + Codex + Cursor making_code_changes

## 流程
1. Understand：glob/grep/read 摸清现状与约定
2. Plan：1-5 步短计划（复杂才写）
3. Implement：最小改动
4. Verify：跑已有测试/命令
5. Deliver：finish 总结

## 改码纪律
- 先读后改
- 跟随项目已有模式，不引入未使用依赖
- 精确替换优于整文件重写
- 不主动建文档
- 不碰无关文件
- 用户没让 revert 就不要回滚别人的改动

## 完成标准
- 行为满足请求
- 有验证证据
- 说明改了什么、怎么验证
