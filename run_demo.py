import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from demo.app import demo

if __name__ == "__main__":
    print("启动语音识别演示系统...")
    print("访问地址: http://127.0.0.1:7860")
    demo.launch(server_name="127.0.0.1", server_port=7860)
