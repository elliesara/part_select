from supabase import create_client
from openai import OpenAI
import numpy as np
import os
from dotenv import load_dotenv
from typing import List, Dict, Any
import logging

load_dotenv()
logger = logging.getLogger(__name__)

class SupabaseRAG:
    """Supabase RAG integration for PartSelect chatbot"""
    
    def __init__(self):
        self.supabase = create_client(
            os.getenv("SUPABASE_URL"), 
            os.getenv("SUPABASE_KEY")
        )
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        # self.client = OpenAI(api_key=os.getenv("API_KEY"), base_url="https://api.deepseek.com")
        

    def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding vector for text using OpenAI's embedding model"""
        try:
            response = self.client.embeddings.create(
                model="text-embedding-3-small",
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Error generating embedding: {e}")
            return None
            

    def store_part_data(self, part_data: Dict[str, Any]) -> bool:
        """Store part data in Supabase with embeddings, avoid duplicates unless content changed"""
        try:
            # Log the incoming data for debugging
            logger.info(f"Attempting to store part: {part_data.get('part_number', 'unknown')}")
            
            # Verify required fields exist
            required_fields = ['name', 'description', 'part_number']
            for field in required_fields:
                if field not in part_data or not part_data[field]:
                    logger.error(f"Missing required field '{field}' for part {part_data.get('part_number', 'unknown')}")
                    return False
            
            # Check if this part already exists in the database
            part_number = part_data['part_number']
            try:
                existing_part = self.supabase.table("parts").select("*").eq("part_number", part_number).execute()
                
                # If part exists, check if content has changed
                if existing_part.data:
                    existing = existing_part.data[0]
                    
                    # Compare essential fields to see if content has changed
                    content_changed = (
                        existing['name'] != part_data['name'] or
                        existing['description'] != part_data['description'] or
                        existing['price'] != part_data['price']
                    )
                    
                    if not content_changed:
                        logger.info(f"Part {part_number} already exists with same content, skipping insert")
                        return True  # Return true because operation is successful (no need to insert)
                    else:
                        logger.info(f"Part {part_number} exists but content changed, updating")
                        # Continue to update below
                else:
                    logger.info(f"Part {part_number} is new, will insert")
            except Exception as e:
                logger.warning(f"Error checking for existing part: {str(e)}")
                # Continue with insertion attempt
            
            # Prepare text for embedding
            text_to_embed = f"{part_data['name']} {part_data['description']} part number {part_data['part_number']}"
            logger.debug(f"Generating embedding for: {text_to_embed[:50]}...")
            
            # Generate embedding
            embedding = self.generate_embedding(text_to_embed)
            if not embedding:
                logger.error("Failed to generate embedding, aborting storage")
                return False
            
            # Prepare data for upsert
            part_record = {
                "part_number": part_data["part_number"],
                "name": part_data["name"],
                "description": part_data["description"],
                "price": part_data["price"],
                "image_url": part_data.get("image_url", ""),
                "difficulty": part_data.get("installation", {}).get("difficulty", ""),
                "time_estimate": part_data.get("installation", {}).get("time_estimate", ""),
                "installation_info": part_data.get("installation", {}).get("schematic_location", ""),
                "embedding": embedding
            }
            
            # Log before upsert
            logger.info(f"Upserting part {part_data['part_number']} into database")
            
            # Upsert into database (insert if not exists, update if exists)
            result = self.supabase.table("parts").upsert(
                part_record,
                on_conflict="part_number"  # Column to determine uniqueness
            ).execute()
            
            # Log success
            logger.info(f"Successfully stored part {part_data['part_number']}")
            return True
        except Exception as e:
            logger.error(f"Error storing part data: {str(e)}", exc_info=True)
            return False
            

    def retrieve_relevant_parts(self, query: str, threshold: float = 0.7, count: int = 3) -> List[Dict]:
        """Retrieve relevant parts based on semantic search"""
        try:
            # Generate embedding for the query
            query_embedding = self.generate_embedding(query)
            
            # Search for similar parts
            response = self.supabase.rpc(
                "match_parts", 
                {
                    "query_embedding": query_embedding, 
                    "match_threshold": threshold, 
                    "match_count": count
                }
            ).execute()
            
            return response.data
        except Exception as e:
            logger.error(f"Error retrieving parts: {e}")
            return []


    def semantic_search(self, table_name: str, query_text: str, match_count: int = 6) -> List[Dict]:
        """Perform semantic search on a specified table"""
        try:
            # Generate embedding for the query
            embedding = self.generate_embedding(query_text)
            if not embedding:
                logger.error("Failed to generate embedding for semantic search")
                return []
            
            # Perform the search using the match_documents RPC function
            result = self.supabase.rpc(
                "match_documents", 
                {
                    "query_embedding": embedding,
                    "match_count": match_count,
                    "table_name": table_name
                }
            ).execute()
            
            if not result.data:
                return []
                
            return result.data
        except Exception as e:
            logger.error(f"Error in semantic search: {str(e)}")
            return []