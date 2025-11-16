#!/usr/bin/env python3
"""Test script to get raw LLM API response for 'hello' based on LLM_PROVIDER env var."""

import asyncio
import json
import os
import time
from dotenv import load_dotenv

load_dotenv()


async def test_claude():
	"""Test Claude API with raw response for 'hello'."""
	from anthropic import AsyncAnthropic
	
	api_key = os.getenv('ANTHROPIC_API_KEY') or os.getenv('CLAUDE_API_KEY')
	if not api_key:
		print("ERROR: ANTHROPIC_API_KEY or CLAUDE_API_KEY not found in environment")
		return None
	
	model = os.getenv('CLAUDE_MODEL', 'claude-sonnet-4-1')
	
	print(f"Testing Claude API with model: {model}")
	print(f"API Key: {'set' if api_key else 'missing'}")
	print("-" * 60)
	
	client = AsyncAnthropic(api_key=api_key)
	start_time = time.time()
	
	try:
		response = await client.messages.create(
			model=model,
			max_tokens=1024,
			messages=[{"role": "user", "content": "hello"}]
		)
		
		elapsed_time = time.time() - start_time
		
		print(f"\n✅ Response received in {elapsed_time:.2f} seconds")
		print("-" * 60)
		print("\n📄 RAW RESPONSE OBJECT:")
		print("=" * 60)
		print(json.dumps({
			"id": response.id,
			"model": response.model,
			"type": response.type,
			"role": response.role,
			"content": [{"type": block.type, "text": getattr(block, 'text', str(block))} for block in response.content],
			"stop_reason": response.stop_reason,
			"stop_sequence": response.stop_sequence,
			"usage": {
				"input_tokens": response.usage.input_tokens,
				"output_tokens": response.usage.output_tokens,
			} if hasattr(response, 'usage') else None,
		}, indent=2))
		
		print("\n" + "=" * 60)
		print("\n💬 EXTRACTED TEXT:")
		print("-" * 60)
		if response.content:
			for block in response.content:
				if hasattr(block, 'text'):
					print(block.text)
		
		print("\n" + "=" * 60)
		print(f"\n⚡ Performance: {elapsed_time:.2f}s")
		if hasattr(response, 'usage'):
			print(f"📊 Tokens: {response.usage.input_tokens} input + {response.usage.output_tokens} output = {response.usage.input_tokens + response.usage.output_tokens} total")
		
		return response
		
	except Exception as e:
		elapsed_time = time.time() - start_time
		print(f"\n❌ Error after {elapsed_time:.2f} seconds:")
		print(f"   {type(e).__name__}: {e}")
		import traceback
		traceback.print_exc()
		return None


async def test_gemini():
	"""Test Gemini API with raw response for 'hello'."""
	from google import genai
	
	api_key = os.getenv('GEMINI_API_KEY')
	if not api_key:
		print("ERROR: GEMINI_API_KEY not found in environment")
		return None
	
	model = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')
	
	print(f"Testing Gemini API with model: {model}")
	print(f"API Key: {'set' if api_key else 'missing'}")
	print("-" * 60)
	
	client = genai.Client(api_key=api_key)
	start_time = time.time()
	
	try:
		response = client.models.generate_content(
			model=model,
			contents="hello",
			config={
				"max_output_tokens": 1024,
				"temperature": 0.3,
			}
		)
		
		elapsed_time = time.time() - start_time
		
		print(f"\n✅ Response received in {elapsed_time:.2f} seconds")
		print("-" * 60)
		print("\n📄 RAW RESPONSE OBJECT:")
		print("=" * 60)
		
		# Convert response to dict for JSON serialization
		response_dict = {
			"model": model,
			"text": response.text if hasattr(response, 'text') else str(response),
		}
		
		# Try to get additional response metadata
		if hasattr(response, 'candidates'):
			response_dict["candidates"] = [
				{
					"index": getattr(c, 'index', None),
					"content": {
						"parts": [{"text": part.text if hasattr(part, 'text') else str(part)} for part in (getattr(c.content, 'parts', []) if hasattr(c, 'content') else [])],
						"role": getattr(getattr(c, 'content', None), 'role', None) if hasattr(c, 'content') else None,
					} if hasattr(c, 'content') else None,
					"finish_reason": getattr(c, 'finish_reason', None),
				}
				for c in response.candidates
			]
		
		if hasattr(response, 'usage_metadata'):
			response_dict["usage_metadata"] = {
				"prompt_tokens": getattr(response.usage_metadata, 'prompt_token_count', None),
				"candidates_tokens": getattr(response.usage_metadata, 'candidates_token_count', None),
				"total_tokens": getattr(response.usage_metadata, 'total_token_count', None),
			}
		
		print(json.dumps(response_dict, indent=2, default=str))
		
		print("\n" + "=" * 60)
		print("\n💬 EXTRACTED TEXT:")
		print("-" * 60)
		if hasattr(response, 'text'):
			print(response.text)
		else:
			print(str(response))
		
		print("\n" + "=" * 60)
		print(f"\n⚡ Performance: {elapsed_time:.2f}s")
		if hasattr(response, 'usage_metadata'):
			um = response.usage_metadata
			if hasattr(um, 'total_token_count'):
				print(f"📊 Tokens: {getattr(um, 'prompt_token_count', 0)} input + {getattr(um, 'candidates_token_count', 0)} output = {getattr(um, 'total_token_count', 0)} total")
		
		return response
		
	except Exception as e:
		elapsed_time = time.time() - start_time
		print(f"\n❌ Error after {elapsed_time:.2f} seconds:")
		print(f"   {type(e).__name__}: {e}")
		import traceback
		traceback.print_exc()
		return None


async def test_openai():
	"""Test OpenAI API with raw response for 'hello'."""
	from openai import AsyncOpenAI
	
	api_key = os.getenv('OPENAI_API_KEY')
	if not api_key:
		print("ERROR: OPENAI_API_KEY not found in environment")
		return None
	
	model = os.getenv('OPENAI_MODEL', 'gpt-5-nano')
	
	print(f"Testing OpenAI API with model: {model}")
	print(f"API Key: {'set' if api_key else 'missing'}")
	print("-" * 60)
	
	client = AsyncOpenAI(api_key=api_key)
	start_time = time.time()
	
	try:
		# Newer models (gpt-5, o1, o3) require max_completion_tokens instead of max_tokens
		use_max_completion_tokens = any(prefix in model.lower() for prefix in ['gpt-5', 'o1', 'o3'])
		
		create_params = {
			"model": model,
			"messages": [{"role": "user", "content": "hello"}],
		}
		
		if use_max_completion_tokens:
			create_params["max_completion_tokens"] = 1024
		else:
			create_params["max_tokens"] = 1024
		
		response = await client.chat.completions.create(**create_params)
		
		elapsed_time = time.time() - start_time
		
		print(f"\n✅ Response received in {elapsed_time:.2f} seconds")
		print("-" * 60)
		print("\n📄 RAW RESPONSE OBJECT:")
		print("=" * 60)
		print(json.dumps({
			"id": response.id,
			"model": response.model,
			"object": response.object,
			"created": response.created,
			"choices": [
				{
					"index": choice.index,
					"message": {
						"role": choice.message.role,
						"content": choice.message.content,
					},
					"finish_reason": choice.finish_reason,
				}
				for choice in response.choices
			],
			"usage": {
				"prompt_tokens": response.usage.prompt_tokens,
				"completion_tokens": response.usage.completion_tokens,
				"total_tokens": response.usage.total_tokens,
			} if hasattr(response, 'usage') and response.usage else None,
		}, indent=2))
		
		print("\n" + "=" * 60)
		print("\n💬 EXTRACTED TEXT:")
		print("-" * 60)
		if response.choices and response.choices[0].message.content:
			print(response.choices[0].message.content)
		
		print("\n" + "=" * 60)
		print(f"\n⚡ Performance: {elapsed_time:.2f}s")
		if hasattr(response, 'usage') and response.usage:
			print(f"📊 Tokens: {response.usage.prompt_tokens} input + {response.usage.completion_tokens} output = {response.usage.total_tokens} total")
		
		return response
		
	except Exception as e:
		elapsed_time = time.time() - start_time
		print(f"\n❌ Error after {elapsed_time:.2f} seconds:")
		print(f"   {type(e).__name__}: {e}")
		import traceback
		traceback.print_exc()
		return None


async def main():
	"""Main function that detects LLM_PROVIDER and tests the appropriate API."""
	llm_provider = os.getenv('LLM_PROVIDER', 'gemini').strip().lower()
	
	print("=" * 60)
	print(f"🧪 Testing LLM Provider: {llm_provider.upper()}")
	print("=" * 60)
	print()
	
	if llm_provider == 'claude':
		await test_claude()
	elif llm_provider == 'gemini':
		await test_gemini()
	elif llm_provider == 'openai':
		await test_openai()
	else:
		print(f"ERROR: Unsupported LLM_PROVIDER '{llm_provider}'")
		print("Supported providers: 'claude', 'gemini', 'openai'")
		print("Set LLM_PROVIDER environment variable to choose a provider")


if __name__ == '__main__':
	asyncio.run(main())

