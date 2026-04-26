from openai import OpenAI, OpenAIError
import httpx
import os
import re
from config import config

class Translator:
    def __init__(self, api_key=None, base_url=None, model="MBZUAI-IFM/K2-Think-nothink", target_lang="Chinese"):
        """
        Translates text using an LLM.
        
        Args:
            api_key: OpenAI API Key (or set OPENAI_API_KEY env var).
            base_url: Optional base URL (e.g. for local generic server like Ollama/LMStudio).
            model: Model name to use.
            target_lang: The target language for translation.
        """
        self.target_lang = target_lang
        self.model = model
        
        # If no key provided, check env. If still none, we might be in local mode (no auth) or fail.
        # Some local servers don't need a valid key, but the client requires a string.
        if not api_key:
            api_key = os.getenv("OPENAI_API_KEY", "")
            
        if not base_url:
            base_url = os.getenv("OPENAI_BASE_URL")

        self.base_url = base_url
        
        # Create HTTP client with SSL verification disabled (for self-signed certs)
        http_client = httpx.Client(verify=False)
        self.client = OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
        
        # Logging
        print(f"[Translator] Initialized:")
        print(f"  - Base URL: {base_url or 'https://api.openai.com/v1 (default)'}")
        print(f"  - Model: {model}")
        print(f"  - Target Language: {target_lang}")
        print(f"  - API Key: {api_key[:8]}...{api_key[-4:] if len(api_key) > 12 else '***'}")
        
        # Context carryover for sentence continuity (History Queue)
        from collections import deque
        self.history = deque(maxlen=3) # Keep context short for faster requests
        self.last_usage = None

    def _supports_deepseek_thinking_toggle(self):
        """Detect DeepSeek-compatible endpoints that support the thinking switch."""
        base_url = (self.base_url or "").lower()
        model = (self.model or "").lower()
        return "deepseek.com" in base_url or model.startswith("deepseek-")

    def _extra_body(self):
        """Request extras for provider-specific compatibility."""
        if self._supports_deepseek_thinking_toggle():
            return {"thinking": {"type": "disabled"}}
        return None

    def _build_system_prompt(self, mode="primary", extra_context=None, context_limit=None):
        """Build a shorter prompt for primary translation and a richer one for corrections."""
        system_prompt = config.translation_prompt.replace('{target_lang}', self.target_lang)

        if mode == "primary":
            system_prompt += (
                "\n请优先输出简短、可直接显示的字幕译文。"
                "如果原文还没有完全收束，也先翻译当前可理解部分，不要等待整句完全结束。"
            )
        elif mode == "correction":
            system_prompt += (
                "\n请结合后续语境修正上一句字幕译文，尽量消除因断句过早带来的偏差。"
                "如果发现前文需要重新断开，请优先保证当前这段话的整体可读性。"
            )

        context_items = []
        if extra_context:
            if isinstance(extra_context, str):
                context_items.append(extra_context.strip())
            else:
                context_items.extend([item.strip() for item in extra_context if item and item.strip()])

        if self.history and (context_limit is None or context_limit > 0):
            limit = context_limit or len(self.history)
            recent = list(self.history)[-limit:]
            for h_orig, h_trans in recent:
                context_items.append(f"\"{h_orig}\" -> \"{h_trans}\"")

        if context_items:
            system_prompt += "\n最近语境记录:\n" + "\n".join(context_items) + "\n"

        return system_prompt

    def _strip_thinking(self, text):
        """Remove <think>...</think> tags from response (for reasoning models)"""
        # Remove think tags and their content
        cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
        return cleaned.strip()

    def _usage_to_dict(self, usage):
        if not usage:
            return None
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
            "estimated": False,
        }

    def _estimate_tokens(self, text):
        if not text:
            return 0
        try:
            import tiktoken

            encoder = tiktoken.get_encoding("cl100k_base")
            return len(encoder.encode(text))
        except Exception:
            return max(1, len(text) // 2)

    def _estimate_usage(self, system_prompt, text, output_text):
        prompt_tokens = self._estimate_tokens(system_prompt) + self._estimate_tokens(text) + 8
        completion_tokens = self._estimate_tokens(output_text)
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "estimated": True,
        }

    def translate(
        self,
        text,
        use_context=True,
        mode="primary",
        extra_context=None,
        context_limit=None,
        max_tokens=None,
        timeout=None,
        temperature=None,
    ):
        """
        Translates the given text. Returns the translated string.
        Uses conversation history for better continuity.
        """
        if not text or not text.strip():
            return ""

        system_prompt = self._build_system_prompt(
            mode=mode,
            extra_context=extra_context if use_context else None,
            context_limit=context_limit if use_context else 0,
        )

        if max_tokens is None:
            max_tokens = 180 if mode == "primary" else 260
        if timeout is None:
            timeout = 8.0 if mode == "primary" else 12.0
        if temperature is None:
            temperature = 0.15 if mode == "primary" else 0.1

        try:
            request_kwargs = dict(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            extra_body = self._extra_body()
            if extra_body:
                request_kwargs["extra_body"] = extra_body

            response = self.client.chat.completions.create(
                **request_kwargs
            )
            result = response.choices[0].message.content.strip()
            self.last_usage = self._usage_to_dict(getattr(response, "usage", None))
            if self.last_usage is None:
                self.last_usage = self._estimate_usage(system_prompt, text, result)
            
            # Store for history
            self.history.append((text, result))
            
            return result
        except Exception as e:
            print(f"Translation Error: {e}")
            self.last_usage = None
            return text

    def stream_translate(
        self,
        text,
        use_context=True,
        mode="primary",
        extra_context=None,
        context_limit=None,
        max_tokens=None,
        temperature=None,
        timeout=None,
    ):
        """
        Translates text and yields partial results immediately.
        """
        if not text or not text.strip():
            return

        system_prompt = self._build_system_prompt(
            mode=mode,
            extra_context=extra_context if use_context else None,
            context_limit=context_limit if use_context else 0,
        )

        if max_tokens is None:
            max_tokens = 180 if mode == "primary" else 260
        if timeout is None:
            timeout = 8.0 if mode == "primary" else 12.0
        if temperature is None:
            temperature = 0.15 if mode == "primary" else 0.1

        full_content = ""
        try:
            request_kwargs = dict(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                stream_options={"include_usage": True},
                timeout=timeout,
            )
            extra_body = self._extra_body()
            if extra_body:
                request_kwargs["extra_body"] = extra_body

            try:
                response = self.client.chat.completions.create(**request_kwargs)
            except Exception as e:
                if "stream_options" in str(e) or "include_usage" in str(e):
                    request_kwargs.pop("stream_options", None)
                    response = self.client.chat.completions.create(**request_kwargs)
                else:
                    raise
            
            usage = None
            for chunk in response:
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                    continue
                content = chunk.choices[0].delta.content or ""
                if content:
                    full_content += content
                    yield content
            
            # After stream is exhausted, update history for next context
            if full_content.strip():
                self.history.append((text, full_content.strip()))
            self.last_usage = self._usage_to_dict(usage)
            if self.last_usage is None:
                self.last_usage = self._estimate_usage(system_prompt, text, full_content.strip())
                    
        except Exception as e:
            print(f"Stream Translation Error: {e}")
            self.last_usage = None
            yield text

    def refine_document(self, text):
        """
        Polish the entire document for coherence and flow, and suggest a title.
        """
        if not text or not text.strip():
            return "", ""
            
        print("[Translator] Refining final document and generating title...")
        system_prompt = config.refinement_prompt.replace('{target_lang}', self.target_lang)
        
        try:
            request_kwargs = dict(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
                ],
                temperature=0.3,
                max_tokens=4000,
            )
            extra_body = self._extra_body()
            if extra_body:
                request_kwargs["extra_body"] = extra_body

            response = self.client.chat.completions.create(
                **request_kwargs
            )
            raw_response = response.choices[0].message.content.strip()
            
            # Simple parsing
            title = ""
            content = raw_response
            
            if "标题:" in raw_response and "---内容---" in raw_response:
                parts = raw_response.split("---内容---")
                # Extract only the line that starts with '标题:' for filename
                for line in parts[0].split('\n'):
                    if "标题:" in line:
                        title_part = line.replace("标题:", "").strip()
                        break
                else:
                    title_part = parts[0].split('\n')[0].replace("标题:", "").strip()
                
                # Clean filename characters
                import re
                title = re.sub(r'[\\/:*?"<>|]', '', title_part).strip()
                content = parts[1].strip()
                # Include summary in the content if it's not already at the top
                if parts[0].strip() not in content:
                    content = parts[0].strip() + "\n\n---\n\n" + content
            
            return title, content
            
        except Exception as e:
            print(f"[Refine Error] {e}")
            return "Transcript", text


if __name__ == "__main__":
    # Test
    print("Testing Translator (simulated)...")
    # This will likely fail if no real server is running, so we wrap in try
    t = Translator(target_lang="Spanish")
    print(t.translate("Hello world"))
