# BeadCode 服务器部署指南

推荐使用 Docker Compose。应用容器只监听服务器本机 `127.0.0.1:8000`，公网流量由 Nginx 通过 HTTPS 转发。

## 1. 上传项目

把整个项目目录上传到服务器，例如 `/opt/bead-code`。不需要上传 `.venv`、`output` 或本地测试图片。

## 2. 启动容器

```bash
cd /opt/bead-code
printf 'BEAD_CODE_PORT=8010\n' > .env
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8010/health
```

健康检查应返回 `{"status":"ok"}`。查看日志：

```bash
docker compose logs -f --tail=100
```

## 3. 配置 Nginx

把 `deploy/nginx.conf.example` 复制到 Nginx 站点目录，将 `qr.example.com` 替换成自己的域名，然后检查并重载：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 4. 配置 HTTPS

域名解析到服务器后，可以使用服务器现有的证书方案；如果已经安装 Certbot：

```bash
sudo certbot --nginx -d qr.example.com
```

不要直接将 `8000` 端口开放到公网。HTTPS 很重要，因为上传内容本身就是有效收款二维码。

## 5. 更新版本

```bash
cd /opt/bead-code
docker compose up -d --build
docker image prune -f
```

上传原图在单次转换完成后立即删除。生成结果位于容器临时内存盘，并在约 30 分钟后清理；容器停止时也会清空仍存活的任务。
