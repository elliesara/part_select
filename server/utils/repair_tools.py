from typing import Dict, Any, List, Optional
from openai import OpenAI
import json
import os
import logging

from server.utils.supabase_client import SupabaseRAG

logger = logging.getLogger(__name__)

class RepairTools:
    def __init__(self, supabase_client):
        self.supabase = supabase_client
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        # self.client = OpenAI(api_key=os.getenv("API_KEY"), base_url="https://api.deepseek.com")


    async def analyze_repair_query(self, user_message: str) -> Dict[str, Any]:
        """
        Use LLM to analyze a repair query for appliances, symptoms, and parts
        """
        try:
            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                # model="deepseek-chat",
                messages=[
                    {
                        "role": "system",
                        "content": """You are an appliance repair expert. 
                        Analyze the user's query and extract structured information in JSON format.
                        Identify the appliance type, symptoms, whether it's asking about parts,
                        and if it's a general inquiry about common problems.
                        
                        Return your analysis as a valid JSON object with the following fields:
                        - appliance_type: The type of appliance mentioned
                        - symptoms: Array of symptoms described
                        - is_parts_query: Boolean indicating if they're asking about parts
                        - is_general_inquiry: Boolean indicating if it's a general question
                        - search_query: An enhanced query for searching repair guides"""
                    },
                    {
                        "role": "user",
                        "content": user_message
                    }
                ],
                response_format={"type": "json_object"}
            )
            
            analysis = json.loads(response.choices[0].message.content)
            
            # Ensure required fields exist
            if "appliance_type" not in analysis:
                analysis["appliance_type"] = None
                
            if "symptoms" not in analysis:
                analysis["symptoms"] = []
                
            if "is_parts_query" not in analysis:
                analysis["is_parts_query"] = False
                
            if "is_general_inquiry" not in analysis:
                analysis["is_general_inquiry"] = False
                
            return {
                "success": True,
                "analysis": analysis
            }
            
        except Exception as e:
            logger.error(f"Error analyzing repair query with LLM: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "analysis": {
                    "appliance_type": None,
                    "symptoms": [],
                    "is_parts_query": False,
                    "is_general_inquiry": False,
                    "search_query": user_message
                }
            }


    async def search_repair_guides(self, query: str, appliance_type: Optional[str] = None, 
                                 threshold: float = 0.65, limit: int = 3) -> Dict[str, Any]:
        """
        Search repair guides using vector embeddings for semantic search
        
        Args:
            query: User's question or description of the problem
            appliance_type: Optional type of appliance to filter results
            threshold: Similarity threshold (0-1)
            limit: Maximum number of results to return
        """
        try:
            # If appliance_type might be misspelled, use LLM to normalize it
            if appliance_type and appliance_type not in ["refrigerator", "dishwasher", "washer", "dryer", "oven", "range", "microwave"]:
                # This might be a misspelled appliance type - use LLM to detect
                appliance_check = await self.detect_appliance_type(appliance_type)
                if appliance_check["detected_type"]:
                    logger.info(f"Corrected misspelled appliance type from '{appliance_type}' to '{appliance_check['detected_type']}'")
                    appliance_type = appliance_check["detected_type"]
            
            # Define appliance mappings
            appliance_mapping = {
                "fridge": "refrigerator",
                "freezer": "refrigerator",
                "dish washer": "dishwasher",
            }
            
            # Normalize the query text itself before generating embeddings
            normalized_query = query
            for variant, standard in appliance_mapping.items():
                if variant in normalized_query.lower():
                    # Replace the variant with the standard name
                    normalized_query = normalized_query.lower().replace(variant, standard)
                    logger.info(f"Normalized query text from '{query}' to '{normalized_query}'")
                    break

            # Generate embedding for the query
            query_embedding = self.supabase.generate_embedding(normalized_query)
            
            if not query_embedding:
                logger.error("Failed to generate embedding for repair guide query")
                return {"success": False, "message": "Failed to generate search embedding", "guides": []}
            
            # Construct the Supabase query with the embedding
            repairs_query = self.supabase.supabase.rpc(
                "match_repairs", 
                {
                    "query_embedding": query_embedding, 
                    "match_threshold": threshold,
                    "match_count": limit * 2  # Get more than needed for filtering
                }
            )
            
            # Apply appliance filter if provided
            if appliance_type:
                repairs_query = repairs_query.eq("appliance_type", appliance_type.lower())
            
            # Execute the query
            result = repairs_query.execute()

            if not result.data:
                logger.info(f"No repair guides found for query: {query}")
                return {"success": True, "message": "No repair guides found", "guides": []}
            
            # Process results
            guides = []
            for item in result.data:
                # Extract YouTube tutorials if available
                youtube_videos = []
                try:
                    if item.get("youtube_tutorials"):
                        youtube_data = item["youtube_tutorials"]
                        if isinstance(youtube_data, str):
                            youtube_videos = json.loads(youtube_data)
                        else:
                            youtube_videos = youtube_data
                except Exception as e:
                    logger.error(f"Error parsing YouTube data: {str(e)}")
                
                # Extract repair solutions if available
                solutions = []
                try:
                    if item.get("repair_solutions"):
                        solutions_data = item["repair_solutions"]
                        if isinstance(solutions_data, str):
                            solutions = json.loads(solutions_data)
                        else:
                            solutions = solutions_data
                except Exception as e:
                    logger.error(f"Error parsing repair solutions: {str(e)}")
                
                guides.append({
                    "id": item.get("id", ""),
                    "appliance_type": item.get("appliance_type", "unknown"),
                    "symptom": item.get("symptom", ""),
                    "symptom_id": item.get("symptom_id", ""), 
                    "overview": item.get("overview", ""),
                    "difficulty": item.get("difficulty", "Unknown"),
                    "similarity": item.get("similarity", 0.0),
                    "url": item.get("url", ""),
                    "root_cause": item.get("root_cause", ""),
                    "youtube_tutorials": youtube_videos,
                    "repair_solutions": solutions,
                    "tools_needed": item.get("tools_needed", []),
                    "safety_precautions": item.get("safety_precautions", []),
                    "key_symptoms": item.get("key_symptoms", [])
                })
            
            # Sort by similarity score and limit results
            guides = sorted(guides, key=lambda x: x["similarity"], reverse=True)[:limit]
            
            return {
                "success": True,
                "guides": guides,
                "message": f"Found {len(guides)} relevant repair guides"
            }
            
        except Exception as e:
            logger.error(f"Error searching repair guides: {str(e)}")
            return {
                "success": False,
                "message": f"Error searching repair guides: {str(e)}",
                "guides": []
            }
    

    async def generate_repair_summary(self, guide_id: str) -> Dict[str, Any]:
        """
        Generate a summary of a repair guide using the guide ID
        
        Args:
            guide_id: The ID of the repair guide in Supabase
        """
        try:
            # Fetch the repair guide data
            result = self.supabase.supabase.table("repairs").select("*").eq("id", guide_id).execute()
            
            if not result.data:
                return {
                    "success": False,
                    "message": f"No repair guide found with ID {guide_id}",
                    "summary": "",
                    "url": ""
                }
            
            guide = result.data[0]
            
            repair_solutions = []
            try:
                if guide.get("repair_solutions"):
                    solutions_data = guide["repair_solutions"]
                    if isinstance(solutions_data, str):
                        repair_solutions = json.loads(solutions_data)
                    else:
                        repair_solutions = solutions_data
            except Exception as e:
                logger.error(f"Error parsing repair solutions: {str(e)}")
            
            # Extract YouTube tutorials
            youtube_tutorials = []
            try:
                if guide.get("youtube_tutorials"):
                    youtube_data = guide["youtube_tutorials"]
                    if isinstance(youtube_data, str):
                        youtube_tutorials = json.loads(youtube_data)
                    else:
                        youtube_tutorials = youtube_data
            except Exception as e:
                logger.error(f"Error parsing YouTube data: {str(e)}")
            
            # Build context for summary generation
            context = f"Appliance: {guide['appliance_type']}\n"
            context += f"Symptom: {guide['symptom']}\n\n"
            context += f"Overview: {guide['overview']}\n"
            
            # Add repair solutions
            if repair_solutions:
                context += "Repair Solutions:\n"
                for i, solution in enumerate(repair_solutions, 1):
                    context += f"Solution {i}: {solution.get('name', '')}\n"
                    context += f"{solution.get('content', '')}\n\n"
                    
                    if solution.get("repair_solutions"):
                        for step in solution["repair_solutions"]:
                            context += f"- {step.get('title', '')}: {step.get('content', '')}\n"
            
            # Generate a concise summary using OpenAI
            response = self.client.chat.completions.create(
                model="gpt-4o",
                # model="deepseek-chat",
                messages=[
                    {
                        "role": "system",
                        "content": """
                        You are a repair expert. Create a concise, helpful summary of this repair guide that explains the problem, likely causes, and key repair steps.
                        Keep it easy to understand but not terse. Don't cut off sentences; better to leave out details than to end mid-sentence.
                        
                        FORMAT YOUR RESPONSE EXACTLY AS FOLLOWS IN MARKDOWN:

                        I found these repair guides that might help!
                        <br>
                        **Summary:** [concise summary of the problem and likely causes] Start by looking at:
                        <br>
                        **Solution [number]: [Part Name]**
                        - [Step 1 for fixing/diagnosing this part]
                        - [Step 2 for fixing/diagnosing this part]
                        - [Step 3 for fixing/diagnosing this part]
                        - [etc.]

                        IMPORTANT:
                        1. Maximum 3 solutions. Each have to have at least 2 steps.
                        2. USE MARKDOWN
                        3. Prioritize the most likely fixes first
                        4. Use complete sentences that don't get cut off.
                        5. Make sure your entire response fits within token limit.
                        """ 
                    },
                    {
                        "role": "user",
                        "content": context
                    }
                ],
                max_tokens=350
            )
            
            # Extract and clean the summary
            summary = response.choices[0].message.content.strip()
            
            # Check for YouTube tutorial to include in response
            video_title = ""
            video_url = ""
            if youtube_tutorials:
                video_title = youtube_tutorials[0].get("title", "")
                video_url = youtube_tutorials[0].get("url", "")
            
            # Return the summary and related details
            return {
                "success": True,
                "summary": summary,
                "url": guide["url"],
                "video_title": video_title,
                "video_url": video_url
            }
            
        except Exception as e:
            logger.error(f"Error generating repair summary: {str(e)}")
            return {
                "success": False,
                "message": f"Error generating repair summary: {str(e)}",
                "summary": "",
                "url": ""
            }


    async def generate_part_recommendation(self, symptom: str, appliance_type: str) -> Dict[str, Any]:
        """
        Generate part recommendations for a specific symptom and appliance type
        
        Args:
            symptom: The symptom or issue the user is experiencing
            appliance_type: The type of appliance (refrigerator, dishwasher, etc.)
        """
        try:
            # First search for relevant repair guides
            guides_result = await self.search_repair_guides(
                query=f"{appliance_type} {symptom}",
                appliance_type=appliance_type,
                threshold=0.6,
                limit=2
            )
            
            if not guides_result["success"] or not guides_result["guides"]:
                return {
                    "success": False,
                    "message": f"No repair guides found for {appliance_type} with symptom: {symptom}",
                    "recommendations": []
                }
            
            # Collect parts from the guides
            all_parts = []
            for guide in guides_result["guides"]:
                if guide.get("parts_to_consider"):
                    all_parts.extend(guide["parts_to_consider"])
            
            # If we found parts, return them
            if all_parts:
                return {
                    "success": True,
                    "message": f"Found {len(all_parts)} parts that might fix your {symptom} issue",
                    "recommendations": all_parts
                }
            
            # If no parts found in guides, generate recommendations using LLM
            context = f"Appliance: {appliance_type}\nSymptom: {symptom}\n\n"
            for guide in guides_result["guides"]:
                context += f"Repair Guide: {guide['symptom']}\n"
                context += f"Overview: {guide['overview']}\n"
                if guide.get("root_cause"):
                    context += f"Root cause: {guide['root_cause']}\n"
                # context += "\n"
            
            response = self.client.chat.completions.create(
                model="gpt-4o",
                # model = "deepseek-chat",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a repair expert. Based on the symptom description and repair guides, " +
                                  "suggest likely parts that might need replacement to fix the issue."
                    },
                    {
                        "role": "user", 
                        "content": context
                    }
                ],
                response_format={"type": "json_object"}
            )
            
            recommendation_json = json.loads(response.choices[0].message.content)
            
            return {
                "success": True,
                "message": "Generated part recommendations based on symptom analysis",
                "recommendations": recommendation_json.get("parts", [])
            }
            
        except Exception as e:
            logger.error(f"Error generating part recommendations: {str(e)}")
            return {
                "success": False,
                "message": f"Error generating part recommendations: {str(e)}",
                "recommendations": []
            }


    async def detect_appliance_type(self, text: str) -> Dict[str, Any]:
        """
        Use LLM to detect appliance type even with spelling errors
        
        Args:
            text: Text that might contain a misspelled appliance type
        """
        try:
            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",  # Using smaller model for speed
                # model = "deepseek-chat",
                messages=[
                    {
                        "role": "system",
                        "content": """You are an appliance detection system. 
                        Your job is to determine if the user is referring to a common household appliance,
                        even if it's misspelled. Map any appliance reference to one of these standard types:
                        
                        refrigerator (including fridge, freezer, ice maker, cooler, refridgerator, etc.)
                        dishwasher (including dish washer, dish-washer, etc.)
                        washer (including washing machine, clothes washer, etc.)
                        dryer (including clothes dryer, etc.)
                        oven (including convection oven, etc.)
                        range (including stove, cooktop, etc.)
                        microwave (including microwave oven, etc.)
                        garbage_disposal (including disposal)
                        
                        Only return the standard type name, or null if no appliance is mentioned."""
                    },
                    {
                        "role": "user",
                        "content": text
                    }
                ]
            )
            
            detected_type = response.choices[0].message.content.strip().lower()
            
            # Handle non-matches or responses that aren't just the appliance type
            if "null" in detected_type or "none" in detected_type or len(detected_type) > 15:
                detected_type = None
                
            return {
                "success": True,
                "detected_type": detected_type
            }
            
        except Exception as e:
            logger.error(f"Error detecting appliance type: {str(e)}")
            return {
                "success": False,
                "detected_type": None
            }


    async def list_common_problems(self, appliance_type: str) -> Dict[str, Any]:
        """
        List common problems/symptoms for a specific appliance type
        
        Args:
            appliance_type: The type of appliance (refrigerator, dishwasher, etc.)
        """
        try:
            # Normalize appliance type
            if appliance_type.lower() in ["fridge", "refrigerator"]:
                appliance_type = "refrigerator"
                
            # Query all symptoms for this appliance type
            result = self.supabase.supabase.table("repairs").select(
                "symptom", "overview", "root_cause"
            ).eq("appliance_type", appliance_type.lower()).execute()
            
            if not result.data or len(result.data) == 0:
                return {
                    "success": False,
                    "message": f"No common problems found for {appliance_type}",
                    "symptoms": []
                }
            
            # Group and sort by frequency
            symptoms_dict = {}
            for item in result.data:
                symptom = item.get("symptom")
                if symptom not in symptoms_dict:
                    symptoms_dict[symptom] = {
                        "symptom": symptom,
                        "overview": item.get("overview", ""),
                        "root_cause": item.get("root_cause", ""),
                        "count": 1
                    }
                else:
                    symptoms_dict[symptom]["count"] += 1
                    
            # Convert to list and sort by count
            symptoms = list(symptoms_dict.values())
            symptoms.sort(key=lambda x: x["count"], reverse=True)
            
            return {
                "success": True,
                "message": f"Found {len(symptoms)} common problems for {appliance_type}",
                "symptoms": symptoms[:8]  # Return top most common
            }
            
        except Exception as e:
            logger.error(f"Error listing common problems: {str(e)}")
            return {
                "success": False,
                "message": f"Error listing common problems: {str(e)}",
                "symptoms": []
            }
