from langgraph.graph import StateGraph, START, END
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from typing import TypedDict, Optional
from dotenv import load_dotenv
import os, json, math, re
import logging

from .utils.supabase_client import SupabaseRAG
from .utils.supabase_tools import SupabaseTools
from .utils.repair_tools import RepairTools
from .utils.scraper_tools import PartSelectScraper as ps

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BASE_URL = os.getenv('BASE_URL')
API_KEY = os.getenv('API_KEY')


# ---------- Agents / Tools ----------
supabase_rag = SupabaseRAG()
supabase_tools = SupabaseTools(supabase_rag)
repair_tools = RepairTools(supabase_rag)

chat_agent = Agent(
    model=OpenAIModel('gpt-4o', provider='openai'),
    # model=OpenAIModel('deepseek-chat', provider=DeepSeekProvider(api_key=API_KEY)),
    system_prompt="""You are an e-commerce website PartSelect's intelligent chatbot. Answer user queries about part
                   numbers, model numbers, installation/fix questions using the retrieved product information.
                   If relevant context is provided, use it to answer the question. If not, ask clarifying questions."""
)

router_agent = Agent(
    model=OpenAIModel('o3-mini', provider='openai'),
    # model=OpenAIModel('deepseek-chat', provider=DeepSeekProvider(api_key=API_KEY)),
    system_prompt='You are a routing agent that determines the next action based on user input.'
)

tools_context = """Available Tables:
- parts: Contains part details for appliances:
     * part_number: Unique identifier/part number
     * part_name: Name of the part
     * mpn_id: Manufacturer part number
     * part_price: Price of the part
     * install_difficulty: Difficulty level of installation
     * install_time: Estimated installation time
     * appliance_types: Type(s) of appliance(s) this part is compatible with
     * replace_parts: Parts that this part can replace
     * brand: Brand of the part
     * availability: Current availability status (in stock?)
     * install_video_url: URL to installation video
     * product_url: URL to product page
     * description: Detailed description of the part
   
- repairs: Locate repairs/parts for specific symptoms:
    * product: Product being repaired (dishwasher, refrigerator, etc.)
    * symptom: Symptom being addressed
    * description: Description of the repair
    * percentage: Percentage of the symptom happening for this product
    * parts: Parts needed for the repair
    * symptom_detail_url: URL to detailed symptom information
    * difficulty: Difficulty level of the repair
    * repair_video_url: URL to repair video

- blogs: Additional resources for troubleshooting and repair:
    * title: Title of the blog post
    * content: Content of the blog post
    * url: URL to the blog post
"""


class AgentState(TypedDict):
    latest_user_message: str
    part_number: Optional[str]
    model_number: Optional[str]
    product_type: Optional[str]
    product_info: Optional[list[str]]
    conversation_history: Optional[list[dict]]
    tool_results: Optional[dict]
    query_type: Optional[str]
    conversation_ended: Optional[bool]


# ---------- Nodes ----------
async def parse_input(state: AgentState) -> AgentState:
    """Parse user input and relevant information, enhanced with RAG."""
    
    prompt = f"""
    {tools_context}

    User input: "{state['latest_user_message']}"

    Based on the user's input, extract the part number (e.g., PS11752778), model number (e.g., WDT780SAEM1), or general question.
    If the user is asking about installation or how to install without specifying a part number, but previously mentioned a\
    part number, use that previous part number.
    If the user is asking about a model number, extract it as well.
    If the user is asking a general question, extract that as well.
    """

    history = state.get('conversation_history', [])
    
    previous_part_number = ""
    previous_model_number = ""
    context_from_history = ""

    previous_part = state.get('part_number', '')
    previous_model = state.get('model_number', '')
    
    logger.info(f"Initial state has part_number: {previous_part}, model_number: {previous_model}")
    logger.info(f"History has {len(history)} previous exchanges")
    
    if history:
        for exchange in reversed(history):
            if exchange.get('part_number') and not previous_part_number:
                previous_part_number = exchange['part_number']
            if exchange.get('model_number') and not previous_model_number:
                previous_model_number = exchange['model_number']
            
            if previous_part_number and previous_model_number:
                break
        
        # Create context string from conversation history
        if previous_part_number or previous_model_number:
            context_from_history = "From our conversation, I remember:\n"
            if previous_part_number:
                context_from_history += f"- You previously asked about part number: {previous_part_number}\n"
            if previous_model_number:
                context_from_history += f"- You mentioned model number: {previous_model_number}\n"
    else:
        context_from_history = ""
    
    # Try to retrieve relevant context from RAG
    relevant_parts = []
    if not state.get('part_number') and len(state['latest_user_message']) > 10:
        # Only do semantic search if no specific part number and message is substantial
        relevant_parts = supabase_rag.retrieve_relevant_parts(
            query=state['latest_user_message'], 
            threshold=0.70
        )
    
    # Build context from retrieved parts
    rag_context = ""
    if relevant_parts:
        rag_context = "Here is information about parts that may be relevant:\n\n"
        for part in relevant_parts[:2]:  # Limit to top 2 results
            rag_context += f"Part: {part['name']} (Part# {part['part_number']})\n"
            rag_context += f"Description: {part['description']}\n"
            rag_context += f"Price: {part['price']}\n"
            if part['difficulty'] and part['time_estimate']:
                rag_context += f"Installation difficulty: {part['difficulty']}\n"
            rag_context += "\n"
    
    combined_context = f"{context_from_history}\n{rag_context}".strip()
    
    prompt = f"""
    User input: "{state['latest_user_message']}"
    
    {combined_context}
    
    Extract the part number (e.g., PS11752778), model number (e.g., WDT780SAEM1), or general question from the input.
    
    If the user is asking about installation or how to install without specifying a part number, but previously mentioned a part number,\
    use that previous part number.
    
    Reply ONLY in this format and do not include backticks:
    {{
        "part_number": "<part number if starting with 'PS' (e.g., 'PS11752778') or from previous conversation if relevant>",
        "model_number": "<model number if mentioned or from previous conversation if relevant>",
        "product_info": "<information about the part or model if applicable (e.g., 'Whirlpool Ice Maker')>",
        "product_type": "<type of product if applicable (e.g., 'fridge', 'washer', etc.)>",
        "general_question": "<general question if applicable (e.g., 'How do I install this?')>"
    }}
    """

    try:
        result = await chat_agent.run(prompt)
        parsed = json.loads(result.output)
        
        # If current message doesn't contain a part number but is asking about installation
        # and previous part number was saved, use previous part number
        is_installation_query = any(word in state['latest_user_message'].lower() for word in ['install', 'how to', 'setup', 'put in', 'replace'])
        
        if not parsed.get('part_number') and is_installation_query and previous_part_number:
            parsed['part_number'] = previous_part_number
            logger.info(f"Using part number {previous_part_number} from conversation history")
        
        # If no model number provided but available in history, use it
        if not parsed.get('model_number') and previous_model_number:
            parsed['model_number'] = previous_model_number
            logger.info(f"Using model number {previous_model_number} from conversation history")
        
        return {
            'latest_user_message': state['latest_user_message'],
            'part_number': parsed.get('part_number', ''),
            'model_number': parsed.get('model_number', ''),
            'product_type': parsed.get('product_type', ''),
            'product_info': parsed.get('product_info', ''),
            'general_question': parsed.get('general_question', ''),
            'conversation_history': state.get('conversation_history', [])
        }
    except Exception as e:
        logger.error(f"Error parsing input: {e}")
        return {**state, 'part_number': '', 'model_number': '', 'product_type': '', 'product_info': '', 'general_question': ''}


def part_lookup(state: AgentState) -> AgentState:
    """Look up part information based on part number."""
    part_number = state['part_number']
    if part_number:
        
        logger.info(f"Looking up part number {part_number}")
        result = ps.search_part_by_number(part_number)
        
        if result["success"]:
            try:
                supabase_success = supabase_rag.store_part_data(result["part_info"])
                logger.info(f"Storing part in Supabase: {'Success' if supabase_success else 'Failed'}")
            except Exception as e:
                logger.error(f"Error storing part in database: {str(e)}")

            part_info = result["part_info"]
            image_html = f"<img src=\"{part_info['image_url']}\" alt=\"Product Image\"  style=\"width: 300px; max-width: 100%; height: auto;\
                border-radius: 5px;\" />\n\n" if part_info['image_url'] else ""
            
            is_installation_query = any(word in state['latest_user_message'].lower() for word in ['install', 'how to', 'setup', 'put in',\
                                                                                                  'replace', 'fix', 'repair'])
            
            if is_installation_query and part_info.get('installation', {}).get('schematic_url'):
                schematic_html = f"<img src=\"{part_info['installation']['schematic_url']}\" alt=\"Installation Diagram\" style=\"width: 300px;\
                                   max-width: 100%; height: auto; border-radius: 5px;\" />\n\n" if part_info['installation']['schematic_url'] else ""
                
                installation_info = [
                    f"**{part_info['name']} Installation Diagram**",
                    schematic_html,
                    f"{part_info['installation'].get('schematic_location', '')}",
                    f"**Difficulty:** {part_info['installation'].get('difficulty', 'Not specified')}",
                    f"**More details can be found here:** {part_info['url']}"
                ]
                state['product_info'] = installation_info
            else:
                rating_stars = ""
                if 'ratings' in part_info and 'rating_percent' in part_info['ratings']:
                    percentage = int(part_info['ratings']['rating_percent'].replace('%', ''))
                    full_stars = math.ceil(percentage / 20)
                    rating_stars = "★" * full_stars + "☆" * (5 - full_stars)

                state['product_info'] = [
                    f"##### {part_info['name']}",
                    image_html,
                    f"**Price: {part_info['price']}** \n\n",
                    f"**Rating:** {rating_stars} ({part_info['ratings'].get('review_count', '0')})",
                    f"**Description:** {part_info['description']}",
                    f"**More details can be found here:** {part_info['url']}"
                ]
        else:
            state['product_info'] = f"Sorry, I couldn't find information for part number {part_number}. {result['message']}"
    return state


def model_lookup(state: AgentState) -> AgentState:
    """Look up compatible parts based on model number."""
    if state.get('model_number'):
        logger.info(f"Looking up model number {state['model_number']}")
        result = ps.search_by_model(state['model_number'])
        
        if result["success"]:
            model_info = result["model_info"]
            try:
                supabase_success = supabase_rag.store_model_data(model_info)
                logger.info(f"Storing model in Supabase: {'Success' if supabase_success else 'Failed'}")
            except Exception as e:
                logger.error(f"Error storing model in database: {str(e)}")
            
            model_url = model_info.get("url", "")
            
            response_info = []
            response_info.append(f"**Model {state['model_number']} Information**")
            
            response_info.append(f"**Appliance Type:** {model_info.get('appliance_type', 'Unknown')}")
            response_info.append(f"**Brand:** {model_info.get('brand', 'Unknown')}")
            
            if model_url:
                response_info.append(f"**Model Details:** [View full specifications]({model_url})")
            
            state['product_info'] = response_info
        else:
            state['product_info'] = f"Sorry, I couldn't find information for model number {state['model_number']}. {result['message']}"
    
    return state


def compatibility_check(state: AgentState) -> AgentState:
    """Check if a part is compatible with a specific model."""
    part_number = state.get('part_number', '')
    model_number = state.get('model_number', '')
    
    if part_number and model_number:
        logger.info(f"Checking compatibility of part {part_number} with model {model_number}")
        
        result = ps.check_compatibility(part_number, model_number)
        
        if result["success"]:
            compatibility_info = result["compatibility_info"]
            
            if compatibility_info["is_compatible"]:
                state['product_info'] = [
                    f"✅ **Good news!** Part **{part_number}** is compatible with your **{compatibility_info.get('appliance_type', 'appliance')}**\
                      (Model: **{model_number}**)."
                ]
            else:
                state['product_info'] = [
                    f"❌ I've checked and unfortunately part **{part_number}** is **not compatible** with model **{model_number}**.",
                    "<br>",
                    "Try asking me other parts and I can check if they're compatible parts with your model."
                ]
        else:
            state['product_info'] = f"Sorry, I couldn't check the compatibility between part {part_number} and model {model_number}. {result['message']}"
    else:
        if not part_number and not model_number:
            state['product_info'] = "To check compatibility, I need both a part number and a model number. Please provide both."
        elif not part_number:
            state['product_info'] = f"I have your model number ({model_number}), but I need a part number to check compatibility.\
                                      Please provide a part number."
        elif not model_number:
            state['product_info'] = f"I have your part number ({part_number}), but I need a model number to check compatibility.\
                                      Please provide your appliance's model number."
    print(state)
    return state


async def diagnose(state: AgentState) -> AgentState:
    """Diagnose an issue based on user input and provide repair guidance from Supabase"""
    user_message = state.get('latest_user_message', '')
    appliance_type = state.get('product_type', '')
    
    if not appliance_type:
        possible_appliances = ["fridge", "refrigerator", "refrig", "dishwasher", 
                              "washer", "dryer", "oven", "stove", "range", "microwave"]
        
        might_have_appliance = any(word in user_message.lower() for word in possible_appliances)
        
        if might_have_appliance or len(user_message.split()) <= 5:
            appliance_check = await repair_tools.detect_appliance_type(user_message)
            if appliance_check.get("detected_type"):
                appliance_type = appliance_check["detected_type"]
                state['product_type'] = appliance_type
    
    general_inquiry_patterns = [
        "what's wrong", "what is wrong", "what could be wrong",
        "problems", "common issues", "troubleshoot",
        "help me with", "diagnose", "not working right", "help",
        "symptoms", "issues with", "what can go wrong", "wrong", "problem", "issue"
    ]
    
    # Common issues detection
    is_general_inquiry = any(pattern in user_message.lower() for pattern in general_inquiry_patterns)
    if appliance_type and is_general_inquiry:
        symptom_result = await repair_tools.list_common_problems(appliance_type)
        # print("\n ------------- \n", "COMMON ISSUES", "\n ------------- \n")
        if symptom_result["success"] and symptom_result["symptoms"]:
            symptoms = symptom_result["symptoms"]
            
            guides_text = []
            guides_text.append(f"Here are the most common issues people experience with their {appliance_type}s:")
            guides_text.append("<br>")
            
            for i, symptom in enumerate(symptoms, 1):
                guides_text.append(f"**{symptom['symptom']}**")
                if symptom.get("overview"):
                    guides_text.append(f"{symptom['overview'][:150]}")
                    guides_text.append("<br>")
            
            guides_text.append("To get more specific help, please tell me which of these issues you're experiencing.")
            state['product_info'] = guides_text
            return state
    
    symptom_map = {
        "not working": ["not working", "doesn't work", "won't work", "stopped working"],
        "leaking": ["leak", "leaky", "leaking", "water", "dripping", "puddle", "wet"],
        "noisy": ["noise", "noisy", "loud", "sound", "rattle", "click", "buzz", "hum"],
        "ice maker": ["ice", "ice maker", "not making ice", "no ice"],
        "cooling": ["not cooling", "warm", "hot", "temperature", "not cold", "too warm"],
        "frozen": ["frozen", "freezing", "too cold", "frost", "ice buildup"],
        "not starting": ["not starting", "won't start", "doesn't start", "power", "turn on", "won't turn on"],
        "door latch failure": ["door latch", "door not closing", "door won't close", "door open", "door ajar"],
        "not cleaning": ["not cleaning", "dirty dishes", "dishes not clean", "residue", "stains", "soap"],
        "not draining": ["not draining", "water not draining", "standing water", "clogged drain"],
        "not drying": ["not drying", "dishes not dry", "moisture", "wet dishes", "steam"],
        "no detergent": ["no detergent", "detergent not dispensing", "detergent not working", "no soap"],
    }
    
    detected_symptoms = []
    for symptom, variants in symptom_map.items():
        if any(variant in user_message.lower() for variant in variants):
            detected_symptoms.append(symptom)
    
    if appliance_type:
        brand_result = await get_brand_specific_repair_info(
            user_message, 
            appliance_type,
            detected_symptoms
        )
        
        if brand_result["success"]:
            state['product_info'] = brand_result["product_info"]
            return state

    has_symptom = len(detected_symptoms) > 0
    
    # Enhance search query for better matching
    search_query = user_message
    if appliance_type and appliance_type not in user_message.lower():
        search_query = f"{appliance_type} {user_message}"
    
    # Add detected symptoms to search query if not already present
    for symptom in detected_symptoms:
        if symptom not in search_query.lower():
            search_query = f"{search_query} {symptom}"
    
    # Determine if this is asking about parts
    is_parts_query = any(word in user_message.lower() for word in ["part", "parts", "replace", "fix", "repair"])
    
    # Supabase semantic search
    if appliance_type and has_symptom and is_parts_query:
        # print("\n ------------- \n", "RECOMMEND PARTS", "\n ------------- \n")
        # This is likely a parts recommendation query
        parts_result = await repair_tools.generate_part_recommendation(user_message, appliance_type)
        if parts_result["success"] and parts_result["recommendations"]:
            parts_list = parts_result["recommendations"]
            parts_text = []
            parts_text.append(f"**Recommended Parts for {appliance_type.title()} Issue**")
            parts_text.append(f"Based on your description: '{user_message}'")
            
            for i, part in enumerate(parts_list, 1):
                part_name = part.get("name", "Unknown part")
                parts_text.append(f"{i}. {part_name}")
                if part.get("category"):
                    parts_text.append(f"- **Category:** {part['category']}")
                if part.get("url"):
                    parts_text.append(f"- [View part details]({part['url']})")
            
            state['product_info'] = parts_text
            return state
    
    # Semantic search on repair guides
    result = await repair_tools.search_repair_guides(
        query=search_query,
        appliance_type=appliance_type,
        threshold=0.60,
        limit=3
    )
    
    if result["success"] and result["guides"]:
        guides = result["guides"]
        guides_text = []
        # print("\n ------------- \n", "GUIDES", "\n ------------- \n")
        # Process each guide
        for i, guide in enumerate(guides, 1):
            summary_result = await repair_tools.generate_repair_summary(guide["id"])
            
            if summary_result["success"]:
                summary = summary_result["summary"]
                url = summary_result["url"]
                video_url = summary_result.get("video_url", "")
                
                guides_text.append(f"{summary}")
                guides_text.append("")

                if video_url:
                    guides_text.append(f"Watch repair video: [{summary_result.get("video_title", "link")}]({video_url})")

                guides_text.append(f"Read full repair guide [here]({url})")
            else:
                guides_text.append(f"**{i}. {guide['symptom']} ({guide['appliance_type'].title()})**")
                guides_text.append(f"- {guide['overview'][:300]}...")
                guides_text.append(f"Read full repair guide [here]({guide['url']})")

        state['product_info'] = guides_text
        return state
    
    analysis_result = await repair_tools.analyze_repair_query(user_message)

    # Search for specific brands
    if not result["success"] or not result["guides"]:
        # print("\n ------------- \n", "BRAND", "\n ------------- \n")
        # Search blogs for brand-specific information
        if appliance_type:
            # Extract brand if mentioned
            brand_specific_results = await get_brand_specific_repair_info(
                user_message,
                appliance_type,
                detected_symptoms
            )

            if brand_specific_results["success"]:
                state['product_info'] = brand_specific_results["product_info"]
                return state

    # LLM FALLBACK
    if analysis_result["success"]:
        # print("\n ------------- \n", "LLM FALLBACK", "\n ------------- \n")
        analysis = analysis_result["analysis"]
        
        # Update appliance type if detected by LLM
        if not appliance_type and analysis.get("appliance_type"):
            appliance_type = analysis["appliance_type"]
            state['product_type'] = appliance_type
        
        # Use enhanced search query from LLM
        llm_search_query = analysis.get("search_query", user_message)
        if appliance_type and appliance_type not in llm_search_query.lower():
            llm_search_query = f"{appliance_type} {llm_search_query}"
        
        # Check if LLM detected symptoms that keywords missed
        llm_symptoms = analysis.get("symptoms", [])
        if llm_symptoms:
            # Semantic search
            result = await repair_tools.search_repair_guides(
                query=llm_search_query,
                appliance_type=appliance_type,
                threshold=0.6,  # Lower threshold for better recall
                limit=3
            )
            
            if result["success"] and result["guides"]:
                guides = result["guides"]
                guides_text = []
                # print("\n ------------- \n", "SUCCESS", "\n ------------- \n")
                # Process each guide
                for i, guide in enumerate(guides, 1):
                    summary_result = await repair_tools.generate_repair_summary(guide["id"])
                    
                    if summary_result["success"]:
                        summary = summary_result["summary"]
                        url = summary_result["url"]
                        video_url = summary_result.get("video_url", "")
                        
                        guides_text.append(f"{summary}")
                        guides_text.append("")
                        
                        if video_url:
                            guides_text.append(f"Watch repair video: [{summary_result.get("video_title", "link")}]({video_url})")

                        guides_text.append(f"Read full repair guide [here]({url})")
                    else:
                        # Fallback to basic info
                        guides_text.append(f"**{i}. {guide['symptom']} ({guide['appliance_type'].title()})**")
                        guides_text.append(f"- **Summary:** {guide['overview'][:300]}...")
                        guides_text.append(f"Read full repair guide [here]({guide['url']})")

                state['product_info'] = guides_text
                return state

    # Fallback message
    state['product_info'] = [
        f"I don't have specific information about '{user_message}' in my repair guides.",
        "<br>",
        "To help you better, please provide:",
        "1. The type of appliance (refrigerator, dishwasher, etc.)",
        "2. The specific symptom or problem you're experiencing",
        "3. Any error codes or unusual behavior"
    ]
    return state


def general_response(state: AgentState) -> AgentState:
    """Provide a general response if no specific action is taken."""
    if not state.get('product_info'):
        state['product_info'] = (
            "I'm here to help with PartSelect inquiries. You can ask me about:\n"
            "- Part information (e.g., 'Tell me about part PS3406971')\n"
            "- Model compatibility (e.g., 'Does part PS3406971 fit model WRS325FDAM04?')\n"
            "- Troubleshooting help (e.g., 'My refrigerator ice maker is not working')\n"
            "How can I assist you today?"
        )
    return state


def get_next_user_input(state: AgentState) -> AgentState:
    """Get the next user input."""
    # In a real application, this would wait for user input
    # Here we simulate it by returning the state unchanged
    return state


async def route_user_input(state: AgentState) -> str:
    """Routes the user input to the appropriate action using LLM reasoning"""

    user_message = state['latest_user_message'].lower()
    farewell_phrases = [
        "thanks", "thank you", "thx", "ty", "thank", "appreciated", 
        "that's all", "goodbye", "bye", "see you", "later",
        "that's it", "that will be all", "all set", "got it"
    ]
    
    if len(user_message.split()) <= 4 and any(phrase in user_message for phrase in farewell_phrases):
        logger.info(f"Detected farewell message: {user_message}")
        return 'farewell'
    
    # Only use the LLM router for more complex decisions
    prompt = f"""
    The user replied: "{state['latest_user_message']}"
    Part number detected: {state.get('part_number', 'None')}
    Model number detected: {state.get('model_number', 'None')}
    
    First, determine if this is likely a conversation ending message like "thanks", "thank you", 
    "that's all", or similar closing remarks.
    
    If it IS a conversation closing message, respond with EXACTLY: farewell
    
    If NOT a closing message, then determine the best routing action. Think through this step by step:
    
    1. NEVER route to part_lookup unless you see a specific part number starting with 'PS'
    2. If the user is asking about a specific model number (like WRS325FDAM04), they need model_lookup
    3. If the user is describing a problem with an appliance, they need diagnose
    4. If none of the above apply, they need general_response
    
    RESPOND WITH EXACTLY ONE OF THESE OPTIONS AND NOTHING ELSE:
    - farewell
    - part_lookup (ONLY IF STARTS WITH 'PS' OR IF USER MENTIONS A PART NUMBER, else model_lookup)
    - model_lookup (for model number questions)
    - compatibility_check
    - diagnose (for appliance problems)
    - general_response (for anything else)
    """
    
    result = await router_agent.run(prompt)
    router_result = result.data.strip().lower()
    
    valid_routes = ['farewell', 'part_lookup', 'model_lookup', 'compatibility_check', 'diagnose', 'general_response']
    if router_result not in valid_routes:
        logger.warning(f"Router returned invalid route: {router_result}. Falling back to general_response")
        return 'general_response'
    
    logger.info(f"Router decided on: {router_result}")
    return router_result


def update_conversation_history(state: AgentState) -> AgentState:
    """Update conversation history with the current exchange data"""
    current_exchange = {
        'user_message': state.get('latest_user_message', ''),
        'response': '\n'.join(state.get('product_info', [])) if isinstance(state.get('product_info'), list) else state.get('product_info', ''),
        'part_number': state.get('part_number', ''),
        'model_number': state.get('model_number', '')
    }
    
    history = state.get('conversation_history', [])
    
    history.append(current_exchange)
    
    if len(history) > 5:
        history = history[-5:]
    
    # Update state with new history
    state['conversation_history'] = history
    
    logger.info(f"Updated conversation history with {current_exchange['part_number']} part number")
    
    return state


async def update_blog_database():
    """Update the blog database with the latest articles"""
    try:
        from server.utils.blog_scraper import scrape_blogs
        logger.info("Starting blog database update...")
        
        count = await scrape_blogs()
        
        logger.info(f"Blog database update complete. Added/updated {count} articles.")
        return {"success": True, "message": f"Added/updated {count} articles."}
    except Exception as e:
        logger.error(f"Error updating blog database: {e}")
        return {"success": False, "message": f"Error: {str(e)}"}


async def get_brand_specific_repair_info(user_message: str, appliance_type: str, symptoms: list = None) -> dict:
    """
    Find brand-specific repair articles and guides for appliance issues.
    
    Args:
        user_message: The user's query
        appliance_type: The type of appliance (refrigerator, dishwasher, etc.)
        symptoms: List of detected symptoms (optional)
        
    Returns:
        Dictionary with success flag and formatted article information if found
    """
    if not appliance_type:
        return {"success": False, "message": "No appliance type detected"}
    
    brand_keywords = ["samsung", "lg", "whirlpool", "ge", "frigidaire", 
                     "kitchenaid", "bosch", "maytag", "kenmore", "electrolux", "amana"]
    
    regex = set(re.findall(r'\b\w+\b', user_message.lower()))
    matches = [brand for brand in brand_keywords if brand in regex]
    if not matches:
        return {"success": False, "message": "No brand-specific articles found"}
    
    print("\n ------------- \n", "BRAND SPECIFIC SEARCH", "\n ------------- \n")
    detected_brand = next((brand for brand in brand_keywords 
                      if re.search(r'\b' + brand + r'\b', user_message.lower())), None)
    
    logger.info(f"Searching for brand-specific info: {detected_brand or 'unknown'} {appliance_type}")
    
    symptoms_text = " ".join(symptoms) if symptoms else ""
    search_query = f"{detected_brand or ''} {appliance_type} {symptoms_text}".strip()
    
    blog_results = await supabase_tools.search_blogs(
        query=search_query,
        appliance_type=appliance_type,
        brand=detected_brand,
        threshold=0.75,  # Higher threshold for more precise matches
        limit=3
    )
    
    if blog_results and blog_results["success"] and blog_results["articles"]:
        articles = blog_results["articles"]
        guides_text = []
        
        guides_text.append(f"Here's what I found that might help with your {appliance_type} issue:")
        guides_text.append("<br>")
        
        for i, article in enumerate(articles, 1):
            guides_text.append(f"**{i}. {article['title']}**")
            
            # Create a brief excerpt from the content
            summary = article['content'][:300] + "..." if len(article['content']) > 300 else article['content']
            guides_text.append(f"{summary}")
            
            guides_text.append(f"**👉 [Read the complete guide]({article['url']})**")
            guides_text.append("<br>")
        
        guides_text.append("If you need more specific help, please provide details about the issue you're facing.")
        
        return {"success": True, "product_info": guides_text}
    
    return {"success": False, "message": "No brand-specific articles found"}


def farewell_response(state: AgentState) -> AgentState:
    """Handle farewell messages like 'thanks' with a polite closing response."""
    farewell_responses = [
        "You're welcome! Feel free to reach out if you need any more help with your appliance needs.",
        "Happy to help! If you have any more questions about parts or repairs, just let me know.",
        "Glad I could assist! Is there anything else you'd like to know about your appliance?",
        "You're welcome! Remember, I'm here whenever you need help with appliance parts or repairs."
    ]
    
    import random
    state['product_info'] = random.choice(farewell_responses)
    state['conversation_ended'] = True
    
    return state


# ---------- Graph ----------
builder = StateGraph(AgentState)
builder.add_edge(START, 'parser')
builder.add_node('parser', parse_input)
builder.add_node('part_lookup', part_lookup)
builder.add_node('model_lookup', model_lookup)
builder.add_node('compatibility_check', compatibility_check)
builder.add_node('diagnose', diagnose)
builder.add_node('general_response', general_response)
builder.add_node('update_history', update_conversation_history)
builder.add_node('farewell', farewell_response)


builder.add_conditional_edges('parser', route_user_input, {
    'farewell': 'farewell',
    'part_lookup': 'part_lookup',
    'model_lookup': 'model_lookup',
    'compatibility_check': 'compatibility_check',
    'diagnose': 'diagnose',
    'general_response': 'general_response'
})

# Replace current END connections with:
builder.add_edge('part_lookup', 'update_history')
builder.add_edge('model_lookup', 'update_history')
builder.add_edge('compatibility_check', 'update_history')
builder.add_edge('diagnose', 'update_history')
builder.add_edge('general_response', 'update_history')

# Connect update_history to END
builder.add_edge('update_history', END)

agentic_flow = builder.compile()