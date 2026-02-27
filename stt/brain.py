import os
from openai import OpenAI
from prompt import SYSTEM_PROMPT

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ.get("OPENROUTER_KEY", ""),
)

def ask_friday_with_ui(user_input, ui_data):
    # Prompt clean rakho
    full_prompt = f"{SYSTEM_PROMPT}\n\n[SCREEN DATA]:\n{ui_data}"
    
    try:
        response = client.chat.completions.create(
            model="openai/gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": full_prompt},
                {"role": "user", "content": user_input}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        return "Sir, brain link down."
        
def verify_action(user_input, state_before, state_after):
    # Prompt for verification
    prompt = f"""
    Boss wanted to: {user_input}
    SCREEN BEFORE: {state_before}
    SCREEN AFTER: {state_after}

    TASK:
    1. Compare BEFORE and AFTER. 
    2. If the requested app (like Chrome) is NOT in AFTER, it means the command FAILED.
    3. If FAILED, provide an ALTERNATIVE command. (e.g. if 'google-chrome-stable' failed, try 'google-chrome').
    4. If SUCCESS, say "Task confirmed, Sir. Chrome is now active."
    """
    
    try:
        response = client.chat.completions.create(
            model="openai/gpt-3.5-turbo",
            messages=[{"role": "system", "content": prompt}]
        )
        return response.choices[0].message.content
    except:
        return "Verification link failed, Sir."
