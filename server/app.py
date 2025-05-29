from flask import Flask, request, jsonify
from flask_cors import CORS
import os, uuid
import sys
import traceback
import logging
from dotenv import load_dotenv
import asyncio
from server.main import supabase_rag

conversations = {}

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from server.main import agentic_flow

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


app = Flask(__name__)
CORS(app, supports_credentials=True, origins=["http://localhost:3000"], allow_headers=["Content-Type"])


@app.route("/")
def home():
    return "PartSelect ChatBot Backend Running"


@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json()
        user_input = data.get("message", "")
        conversation_id = data.get("conversation_id", str(uuid.uuid4()))
        
        conversation_history = conversations.get(conversation_id, [])
        
        logger.info(f"Processing request with conversation_id: {conversation_id}")
        logger.info(f"Conversation history has {len(conversation_history)} messages")

        previous_part_number = ""
        previous_model_number = ""
        
        for message in reversed(conversation_history):
            if not previous_part_number and message.get("part_number"):
                previous_part_number = message["part_number"]
                logger.info(f"Found previous part number: {previous_part_number}")
            if not previous_model_number and message.get("model_number"):
                previous_model_number = message["model_number"]
                logger.info(f"Found previous model number: {previous_model_number}")
            if previous_part_number and previous_model_number:
                break
                
        logger.info(f"Previous part: {previous_part_number}, Previous model: {previous_model_number}")
        
        logger.info(f"Processing user input: {user_input}")
        
        # Create initial state with conversation history
        initial_state = {
            "latest_user_message": user_input,
            "part_number": previous_part_number,
            "model_number": previous_model_number,
            "product_type": "",
            "product_info": "",
            "conversation_history": conversation_history
        }
        
        # Run agent async
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Define async function to run
        async def run_agent():
            return await agentic_flow.ainvoke(initial_state)
            
        # Run async function in event loop
        final_state = loop.run_until_complete(run_agent())
        logger.info(f"Final state received: {final_state}")
        loop.close()
        
        # Determine which part of state has result
        if final_state and isinstance(final_state, dict) and 'product_info' in final_state:
            inner_state = final_state
            logger.info("Found product_info in top-level state")
        else:
            # Try nested approach as fallback
            inner_state = None
            if 'part_lookup' in final_state:
                inner_state = final_state['part_lookup']
            elif 'model_lookup' in final_state:
                inner_state = final_state['model_lookup']
            elif 'compatibility_check' in final_state:
                inner_state = final_state['compatibility_check']
            elif 'diagnose' in final_state:
                inner_state = final_state['diagnose']
            elif 'general_response' in final_state: 
                inner_state = final_state['general_response']

        logger.info(f"Using state: {inner_state}")

        # Extract response
        if inner_state and inner_state.get('product_info'):
            product_info = inner_state['product_info']
            logger.info(f"Found product_info: {product_info}")
            if isinstance(product_info, list):
                response = "\n".join(product_info)
            else:
                response = product_info
        else:
            if not inner_state:
                logger.warning("No inner state found in final_state")
            else:
                logger.warning(f"product_info not found in inner_state keys: {inner_state.keys()}")
            response = "I couldn't find specific information about that. Can you provide more details?"

        # Create message record and update conversation history
        new_exchange = {
            "user_message": user_input,
            "response": response
        }
        
        # Save part/model numbers in the conversation
        if inner_state:
            if inner_state.get('part_number'):
                new_exchange["part_number"] = inner_state['part_number']
            elif previous_part_number:
                new_exchange["part_number"] = previous_part_number
                
            if inner_state.get('model_number'):
                new_exchange["model_number"] = inner_state['model_number']
            elif previous_model_number:
                new_exchange["model_number"] = previous_model_number
        
        conversation_history.append(new_exchange)
        conversations[conversation_id] = conversation_history
        
        return jsonify({
            "response": response,
            "conversation_id": conversation_id
        })
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"Error processing request: {str(e)}\n{tb}")
        return jsonify({"response": "I encountered an error processing your request. Please try again with a different query."})


@app.route("/admin/update-blogs", methods=["POST"])
def update_blogs():
    """Admin endpoint to update blog database"""
    try:
        from server.utils.blog_scraper import scrape_blogs
        count = scrape_blogs()
        return jsonify({"success": True, "message": f"Added/updated {count} articles."})
    except Exception as e:
        logger.error(f"Error updating blog database: {e}")
        return jsonify({"success": False, "message": f"Error: {str(e)}"})


@app.route("/test", methods=["GET"])
def test():
    return jsonify({
        "status": "ok",
        "message": "Server is running correctly"
    })


@app.route('/test/store', methods=['GET'])
def test_store():
    """Test endpoint for storing a sample part"""
    try:
        # Create sample part data
        sample_part = {
            "part_number": "PS_TEST_123",
            "name": "Test Part",
            "description": "This is a test part for debugging",
            "price": "$9.99",
            "image_url": "https://example.com/image.jpg",
            "installation": {
                "difficulty": "Easy",
                "time_estimate": "5 minutes",
                "schematic_location": "Test location"
            }
        }
        
        # Try to store it
        result = supabase_rag.store_part_data(sample_part)
        
        return jsonify({
            "success": result,
            "message": "Test part storage attempted",
            "sample": sample_part
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


if __name__ == "__main__":
    logger.info("Starting Flask server on port 8000...")
    app.run(debug=True, port=8000)
