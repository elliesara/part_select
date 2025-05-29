/**
 * Sends a message to the chatbot backend and returns the response
 * @param {string} message - The user's message to send to the chatbot
 * @returns {Promise<string>} - The chatbot's response
 */
export const sendChatMessage = async (message, conversationId = null) => {
  console.log("Sending message to backend:", message, "Conversation ID:", conversationId);
  try {
    // Add explicit base URL for development
    const BASE_URL = process.env.NODE_ENV === 'production' ? '' : 'http://localhost:8000';
    
    const response = await fetch(`${BASE_URL}/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        message: message,
        conversation_id: conversationId
      }),
      // Add these options to handle CORS issues
      credentials: 'same-origin',
      mode: 'cors'
    });
    
    console.log("Response status:", response.status);
    
    if (!response.ok) {
      // Get more detailed error information
      let errorText = "";
      try {
        errorText = await response.text();
      } catch (e) {}
      
      console.error("Error response:", errorText);
      throw new Error(`HTTP error! Status: ${response.status}, Details: ${errorText}`);
    }
    
    const data = await response.json();
    console.log("Response data:", data);
    
    return {
      response: data.response,
      conversationId: data.conversation_id || conversationId
    };
  } catch (error) {
    console.error('Error sending message:', error);
    throw error;
  }
};

/**
 * Legacy function for getting AI messages - kept for compatibility
 * @param {string} userQuery - The user's message
 * @returns {Promise<Object>} - The message object
 */
export const getAIMessage = async (userQuery) => {
  try {
    console.log("Getting AI message for:", userQuery);
    const response = await sendChatMessage(userQuery);
    return {
      role: "assistant",
      content: response
    };
  } catch (error) {
    console.error("Error in getAIMessage:", error);
    // Return more specific error details in the message
    return {
      role: "assistant",
      content: `Sorry, I encountered an error: ${error.message}`
    };
  }
};