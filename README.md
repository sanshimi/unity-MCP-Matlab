### 需要的文件
A requirements.txt folder.<br>
Create a .env folder.<br>

### 配置环境
```
uv init unity-mcp-demo
uv venv
```

### 激活虚拟环境
```
# conda deactivate
.venv\Scripts\Activate.ps1
```

### 安装依赖
```
uv add $(cat requirements.txt)
```

### 运行程序
```
cd local_client
uv run client.py
```

### 本地客户端参考项目
<https://github.com/daveebbelaar/ai-cookbook/tree/main/mcp/crash-course>

### unity-mcp 服务端参考项目
<https://github.com/a-nom-ali/unity-mcp/tree/main/assets/UnityMCP><br>
修改了其中不符合大模型接口定义类型的错误函数*set_animation_parameter*和*create_animation_clip*。 

