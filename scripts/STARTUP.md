# Envoy 启动说明

## 1. 复制环境变量

将 `.env.example` 复制为 `.env`，并按需修改：

- `NEXT_PUBLIC_API_BASE_URL`：前端访问后端的基地址
- `MYSQL_PORT` / `REDIS_PORT` / `MINIO_PORT` / `MINIO_CONSOLE_PORT`：Docker 依赖服务端口
- `BACKEND_PORT` / `FRONTEND_PORT`：如需手动覆盖脚本默认端口

## 2. 启动前检查

- 确保 Docker Desktop 已启动
- 确保 `node` / `npm` 可用
- Windows 环境下避免占用 `8140` 和 `8200` 附近的排除端口段

## 3. 启动方式

在项目根目录运行：

```powershell
.\scripts\start_env.ps1
```

脚本会自动：

- 为 MySQL / Redis / MinIO 选择可用端口
- 后端从 `8140` 起查找可用端口
- 前端从 `8200` 起查找可用端口
- 将最终端口写入 `logs/startup.log`

## 4. 排查问题

如果启动失败，请查看：

- `logs/startup.log`
- `logs/backend-startup.log`
- `logs/frontend-startup.log`

重点关注：

- 端口是否被占用
- Docker 是否运行
- `NEXT_PUBLIC_API_BASE_URL` 是否和后端实际端口一致
