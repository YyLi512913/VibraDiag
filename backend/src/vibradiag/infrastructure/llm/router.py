from . import ollama,deepseek

def chat(model,*args,**kwargs):
    if model.startswith('deepseek/'):
        return deepseek.chat(model.split('/',1)[1],*args,**kwargs)
    if model not in ('qwen3.5:2b','qwen3.5:0.8b'):
        raise ValueError('不支持的模型配置')
    return ollama.chat(model,*args,**kwargs)
