"""
Tool Manager - Tüm araçları yöneten merkezi sistem.
Claude benzeri tool calling altyapısı.
"""

import importlib
import os
import json
import traceback

class ToolManager:
    """Tüm araçları kaydeden, yöneten ve çalıştıran merkez."""
    
    def __init__(self):
        self.tools = {}
        self._load_all_tools()
    
    def _load_all_tools(self):
        """tools/ klasöründeki tüm araçları otomatik yükle."""
        tools_dir = os.path.dirname(os.path.abspath(__file__))
        
        for filename in os.listdir(tools_dir):
            if filename.endswith('.py') and filename not in ('__init__.py', 'tool_manager.py'):
                module_name = filename[:-3]
                try:
                    module = importlib.import_module(f'tools.{module_name}')
                    if hasattr(module, 'TOOL_DEFINITION') and hasattr(module, 'execute'):
                        tool_def = module.TOOL_DEFINITION
                        self.tools[tool_def['name']] = {
                            'definition': tool_def,
                            'execute': module.execute
                        }
                        print(f"  [OK] Tool yuklendi: {tool_def['name']}")
                except Exception as e:
                    print(f"  [ERROR] Tool yuklenemedi ({module_name}): {e}")
    
    def get_all_definitions(self):
        """Tüm tool tanımlarını döndür (AI'a gönderilecek format)."""
        return [t['definition'] for t in self.tools.values()]
    
    def get_tool_names(self):
        """Tüm tool isimlerini döndür."""
        return list(self.tools.keys())
    
    def get_tool_info(self):
        """Tüm tool'ların bilgilerini UI için döndür."""
        info = []
        for name, tool in self.tools.items():
            info.append({
                'name': name,
                'description': tool['definition'].get('description', ''),
                'emoji': tool['definition'].get('emoji', '🔧'),
                'parameters': tool['definition'].get('parameters', {})
            })
        return info
    
    def execute_tool(self, tool_name, parameters=None):
        """Belirtilen aracı çalıştır."""
        if tool_name not in self.tools:
            return {
                'success': False,
                'error': f"Tool '{tool_name}' was not found.",
                'available_tools': self.get_tool_names()
            }
        
        try:
            result = self.tools[tool_name]['execute'](parameters or {})
            return {
                'success': True,
                'tool': tool_name,
                'result': result
            }
        except Exception as e:
            return {
                'success': False,
                'tool': tool_name,
                'error': str(e),
                'traceback': traceback.format_exc()
            }
    
    def build_system_prompt(self):
        """AI'a gonderilecek tool bilgilerini iceren system prompt."""
        tools_info = []
        for name, tool in self.tools.items():
            t = tool["definition"]
            schema = json.dumps(t.get("parameters", {}), ensure_ascii=False)
            tools_info.append(
                f"- {name}: {t['description']}\n  parameters: {schema}"
            )

        tools_text = "\n".join(tools_info)

        return f"""Available tools:
{tools_text}

Tool protocol:
1. Built-in function calling is not available. Tools only run when you print a
   valid `gazi_tool` code block.
2. Tool block format:
```gazi_tool
{{"tool": "tool_name", "params": {{"parameter": "value"}}}}
```
3. Multiple tool calls may be sent as a JSON array in a single block.
4. Use registered tool names exactly as listed.
5. For code/file tasks, prefer `file_manager` writes instead of pasting long
   code in chat. Use `file_manager` read/list to inspect files before edits.
   For command execution and validation, use `shell_executor`.
6. If a tool fails, explain the failure clearly and suggest the next step.
7. File paths must be workspace-relative. Never use `/Desktop`, `~/Desktop`,
   drive letters, or absolute paths. If the user says Desktop, use a relative
   project folder like `hesapcik/app.py`.
8. JSON must be valid; escape quotes inside HTML/JS/Python content strings.
9. After code edits, run appropriate checks when useful: `python -m py_compile file.py`,
   `python file.py`, `node --check file.js`, `npm run build --prefix app`, or `pytest`.
   You may pass `"cwd": "relative/project-folder"` for commands that must run
   inside a generated project. The application permission layer will approve,
   block, or defer commands.
10. Answer Turkish by default."""
