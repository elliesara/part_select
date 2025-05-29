import logging
import re, json, os
from datetime import datetime
from typing import Dict, Any, List
from openai import OpenAI

from server.utils.scraper_tools import PartSelectScraper
from server.utils.supabase_client import SupabaseRAG


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BASE_REPAIR_URL = "https://www.partselect.com/Repair/"


class RepairKnowledgeScraper:
    """Scraper for appliance repair knowledge"""
    
    @classmethod
    def get_appliance_types(cls) -> List[str]:
        """Get list of available appliance types for repairs"""
        return [
            "Dishwasher",
            "Refrigerator"
        ]
    

    @classmethod
    def get_symptoms_for_appliances(cls, appliance_type: str) -> List[Dict[str, str]]:
        """Get common symptoms/problems for a specific appliance type"""
        url = f"{BASE_REPAIR_URL}{appliance_type}/"
        
        logger.info(f"Retrieving symptoms for {appliance_type}")
        result = PartSelectScraper.get_page_content(url)

        if not result:
            logger.error(f"Failed to retrieve symptoms for {appliance_type}")
            return []
        
        soup, _ = result
        symptoms = []

        # Find symptom links on the page
        symptom_links = soup.select("div.symptom-list > a")
        for link in symptom_links:
            symptom_url = link["href"] if link.has_attr("href") else ""
            if not symptom_url:
                continue
                
            # symptom_name = link.text.strip()
            title_elem = link.select_one("h3.title-md")
            symptom_name = title_elem.text.strip() if title_elem else ""
            
            # Get the description
            desc_elem = link.select_one("p")
            description = desc_elem.text.strip() if desc_elem else ""
            
            # Get the percentage reported
            percent_elem = link.select_one("div.symptom-list__reported-by span:last-child")
            percentage = percent_elem.text.strip() if percent_elem else ""
            if not symptom_name:
                continue
                
            # Extract the symptom identifier from URL
            symptom = symptom_url.split("/")[-2] if len(symptom_url.split("/")) >= 2 else ""
            
            symptoms.append({
                "name": symptom_name,
                "id": symptom,
                "url": symptom_url if symptom_url.startswith("http") else f"https://www.partselect.com{symptom_url}",
                "description": description,
                "percentage": percentage
            })
        
        logger.info(f"Found {len(symptoms)} symptoms for {appliance_type}")
        return symptoms
    

    @classmethod
    def extract_repair_guide(cls, appliance_type: str, symptom: Dict[str, str]) -> Dict[str, Any]:
        """Extract repair guide information for a symptom"""
        url = symptom["url"]
        logger.info(f"Extracting repair guide from {url}")
        
        result = PartSelectScraper.get_page_content(url)
        if not result:
            logger.error(f"Failed to extract repair guide from {url}")
            return {
                "success": False,
                "message": f"Failed to load repair guide page for {symptom['name']}"
            }
        
        soup, final_url = result

        overview = symptom.get("description", "")
    
        youtube_tutorials = []
        youtube_elements = soup.select("div.yt-video[data-yt-init]")
        for yt_elem in youtube_elements:
            video_id = yt_elem.get('data-yt-init', '')
            if video_id:
                # Extract the title from the img alt text if available
                img_elem = yt_elem.select_one("img.yt-video__thumb")
                title = img_elem.get('alt', 'Repair Tutorial') if img_elem else 'Repair Tutorial'
                
                youtube_tutorials.append({
                    "title": title,
                    "url": f"https://www.youtube.com/watch?v={video_id}"
                })
                
        # Extract repair parts list if available
        parts_to_consider = []
        parts_section = soup.select_one("div.col-lg-8 h3.title-black-on-gray")
        if parts_section and "Click a Part Below" in parts_section.text:
            part_links = parts_section.parent.select("a.js-scrollTrigger")
            for part_link in part_links:
                part_id = part_link.get('href', '').replace('#', '')
                part_name = part_link.text.strip()
                if part_name:
                    parts_to_consider.append({
                        "id": part_id,
                        "name": part_name
                    })

        # Find all repair sections (each solution for this symptom)
        repair_solutions = []
        solution_sections = soup.select("div.symptom-list h2.section-title")
        
        for solution_heading in solution_sections:
            solution_name = solution_heading.text.strip()
            solution_id = solution_heading.get("id", "")
            
            # Find the detailed solution content that follows this heading
            solution_desc_div = solution_heading.find_next("div", class_="symptom-list__desc")
            if not solution_desc_div:
                continue
                
            # Extract the content from the first column (description and repair_solutions)
            content_div = solution_desc_div.select_one("div.col-lg-6")
            if not content_div:
                continue
                
            # Get all content (paragraphs and lists)
            solution_content = ""
            for elem in content_div.children:
                if elem.name in ["p", "ol", "ul"]:
                    solution_content += elem.get_text(strip=True, separator=" ") + "\n\n"
            
            # Extract repair_solutions specifically
            solution_steps = []
            repair_solution_list = content_div.select_one("ol")
            if repair_solution_list:
                for i, repair_solution_item in enumerate(repair_solution_list.select("li"), 1):
                    solution_steps.append({
                        "title": f"Step {i}",
                        "content": repair_solution_item.get_text(strip=True)
                    })
            
            # Extract images
            images = []
            img_elements = solution_desc_div.select("img.thumb")
            for img in img_elements:
                if img.has_attr("src") and not "data:image" in img["src"]:
                    img_url = img["src"] 
                    images.append(img_url)
                elif img.has_attr("data-src"):
                    img_url = img["data-src"]
                    images.append(img_url)
            
            # Extract recommended parts
            parts = []
            parts_links = content_div.select("a[href*='-']")
            for link in parts_links:
                part_name = link.text.strip()
                part_url = link["href"]
                if not part_url.startswith("http"):
                    part_url = f"https://www.partselect.com{part_url}"
                    
                # Try to extract part numbers
                part_number = ""
                part_section = solution_desc_div.select_one(".symptom-list__desc__top a")
                if part_section and "View All" in part_section.text:
                    part_category = part_section.text.replace("View All ", "").strip()
                    parts.append({
                        "name": part_name,
                        "category": part_category,
                        "url": part_url
                    })
            
            repair_solutions.append({
                "name": solution_name,
                "id": solution_id,
                "content": solution_content,
                "repair_solutions": solution_steps,
                "images": images,
                "parts": parts
            })
        
        # Use LLM to extract structured information from the repair guide
        structured_info = cls.extract_structured_info(
            appliance_type=appliance_type,
            symptom=symptom["name"],
            overview=overview,
            repair_solutions=repair_solutions
        )
        
        return {
            "success": True,
            "repair_info": {
                "appliance_type": appliance_type,
                "symptom": symptom["name"],
                "symptom_id": symptom["id"],
                "url": final_url,
                "overview": overview,
                "repair_solutions": repair_solutions,
                "youtube_tutorials": youtube_tutorials,
                "parts_to_consider": parts_to_consider,
                "structured_info": structured_info
            }
        }
    

    @classmethod
    def extract_structured_info(cls, appliance_type: str, symptom: str, 
                            overview: str, repair_solutions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Use LLM to extract structured information from repair guide text"""
        try:
            client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            # client = OpenAI(api_key=os.getenv("API_KEY"), base_url="https://api.deepseek.com")
            
            # Combine all content
            full_content = f"Appliance: {appliance_type}\nSymptom: {symptom}\n\nOverview: {overview}\n\n"
            
            # Add each repair solution
            for i, solution in enumerate(repair_solutions, 1):
                full_content += f"Solution {i}: {solution['name']}\n{solution['content']}\n\n"
                if solution.get('repair_solutions'):
                    for repair_solution in solution['repair_solutions']:
                        full_content += f"- {repair_solution['title']}: {repair_solution['content']}\n"
                    full_content += "\n"
            
            # Keep content within token limits
            if len(full_content) > 12000:  # Only need ~3000 tokens
                full_content = full_content[:12000] + "..."
            
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                # model = "deepseek-chat",
                messages=[
                    {
                        "role": "system",
                        "content": """Extract structured repair information from the text. 
                        Include: root cause analysis, tools needed, safety precautions, and key symptoms.
                        Return as JSON."""
                    },
                    {
                        "role": "user",
                        "content": full_content
                    }
                ],
                response_format={"type": "json_object"}
            )
            
            structured_data = json.loads(response.choices[0].message.content)
            return structured_data
            
        except Exception as e:
            logger.error(f"Error extracting structured info: {e}")
            return {
                "error": str(e),
                "difficulty": "Unknown",
                "tools_needed": [],
                "safety_precautions": [],
                "key_symptoms": []
            }
    

    @classmethod
    def store_repair_guide(cls, supabase: SupabaseRAG, repair_info: Dict[str, Any]) -> bool:
        """Store repair guide in Supabase"""
        try:
            # Normalize field names for database storage
            db_entry = {
                "appliance_type": repair_info["appliance_type"].lower(),
                "symptom": repair_info["symptom"],
                "symptom_id": repair_info["symptom_id"],
                "url": repair_info["url"],
                "overview": repair_info["overview"],
                "repair_solutions": json.dumps(repair_info.get("repair_solutions", [])),
                # "youtube_tutorials": json.dumps(repair_info.get("youtube_tutorials", [])),
                "youtube_tutorials": repair_info.get("youtube_tutorials", []),
                # "parts_to_consider": json.dumps(repair_info.get("parts_to_consider", [])),
                # "difficulty": repair_info["structured_info"].get("difficulty", "Unknown"),
                "tools_needed": json.dumps(repair_info["structured_info"].get("tools_needed", [])),
                "safety_precautions": json.dumps(repair_info["structured_info"].get("safety_precautions", [])),
                "root_cause": repair_info["structured_info"].get("root_cause", "Unknown"),
                "key_symptoms": json.dumps(repair_info["structured_info"].get("key_symptoms", [])),
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat()
            }

            logger.info(f"Attempting to insert data with keys: {list(db_entry.keys())}")
            
            # Create embedding for semantic search
            embedding_text = f"{repair_info['appliance_type']} {repair_info['symptom']} {repair_info['overview']}"
            
            # Add content from repair solutions for better embeddings
            for solution in repair_info.get("repair_solutions", []):
                embedding_text += f" {solution.get('name', '')} {solution.get('content', '')[:300]}"
            
            embedding = supabase.generate_embedding(embedding_text)
            
            if embedding:
                db_entry["embedding"] = embedding
                logger.info(f"Generated embedding of length {len(embedding)}")
            else:
                logger.warning("Failed to generate embedding")
            
            # Store in repairs table with better error trapping
            try:
                logger.info("Sending request to Supabase...")
                result = supabase.supabase.table("repairs").insert(db_entry).execute()
                logger.info(f"Supabase response status: {result.status_code if hasattr(result, 'status_code') else 'Unknown'}")
                logger.info(f"Stored repair guide for {repair_info['appliance_type']} - {repair_info['symptom']}")
                return True
            except Exception as db_error:
                logger.error(f"Database insertion error: {type(db_error).__name__}: {db_error}")
                # Try to get more details about the error
                if hasattr(db_error, 'response') and hasattr(db_error.response, 'text'):
                    logger.error(f"Error details: {db_error.response.text}")
                return False
            
        except Exception as e:
            logger.error(f"Error storing repair guide: {e}")
            return False
