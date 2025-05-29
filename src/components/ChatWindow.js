import React, { useState, useRef, useEffect } from "react";
import { marked } from "marked";
import { sendChatMessage } from "../api/api";
import "./ChatWindow.css";

function ChatWindow() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [conversationId, setConversationId] = useState(null);
  const messagesEndRef = useRef(null);

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  // Generate a contextual loading message based on user input
  const generateLoadingMessage = (userInput) => {
    const input = userInput.toLowerCase();
    
    // Check for part numbers (PS followed by digits)
    const partNumberMatch = input.match(/ps\d+/i);
    if (partNumberMatch) {
      const partNumber = partNumberMatch[0].toUpperCase();
      
      if (input.includes("install") || input.includes("how to")) {
        return `Finding installation instructions for ${partNumber}`;
      } else if (input.includes("compatible") || input.includes("fit") || input.includes("work with")) {
        return `Checking compatibility for ${partNumber}`;
      } else {
        return `Looking up details for ${partNumber}`;
      }
    }
    
    // Check for model numbers (typically alphanumeric)
    const modelMatch = input.match(/model\s*(?:number|#|no)?:?\s*([a-z0-9]+)/i);
    if (modelMatch) {
      return `Searching for compatible parts for model ${modelMatch[1].toUpperCase()}`;
    }
    
    // Default loading message
    return "Processing your request";
  };
  
  // Format timestamp
  const formatTime = () => {
    const now = new Date();
    return now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  const handleSend = async (message) => {
    if (message.trim() === "" || isLoading) return;
    
    const userMessage = { role: "user", content: message, time: formatTime() };
    setMessages(prev => [...prev, userMessage]);
    setInput("");
    setIsLoading(true);
    
    try {
      const result = await sendChatMessage(message, conversationId);
      setIsLoading(false);
      
      // Save conversation ID for future messages
      if (result.conversationId) {
        setConversationId(result.conversationId);
      }
      
      setMessages(prev => [...prev, { 
        role: "assistant", 
        content: result.response,
        time: formatTime()
      }]);
    } catch (error) {
      setIsLoading(false);
      setMessages(prev => [...prev, { 
        role: "assistant", 
        content: "Sorry, I encountered an error. Please try again.",
        time: formatTime()
      }]);
    }
  };

  return (
    <div className="chat-wrapper">
      <div className="messages-container">
        {messages.map((message, index) => (
          <div key={index} className={`${message.role}-message-container`}>
            <div className={`avatar ${message.role}-avatar`}>
              {message.role === "user" ? "👩🏻" : "🤖"}
            </div>
            {message.content && (
              <div className={`message ${message.role}-message`}>
                <div dangerouslySetInnerHTML={{__html: marked(message.content).replace(/<p>|<\/p>/g, "")}}></div>
                <div className="message-time">{message.time}</div>
              </div>
            )}
          </div>
        ))}
        {isLoading && (
          <div className="assistant-message-container">
            <div className={`avatar assistant-avatar`}>
              🤖
            </div>
            <div className="message assistant-message">
              <div className="loading-indicator">
                {/* {loadingMessage} */}
                <div className="loading-dots">
                  <span></span>
                  <span></span>
                  <span></span>
                </div>
              </div>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>
      <div className="input-area">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask me about your refrigerator or dishwasher, and I'll try to help!"
          onKeyPress={(e) => e.key === "Enter" && handleSend(input)}
        />
        <button className="send-button" onClick={() => handleSend(input)} disabled={isLoading}>
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 600">
            <path d="M214.6 41.4c-12.5-12.5-32.8-12.5-45.3 0l-160 160c-12.5 12.5-12.5 32.8 0 45.3s32.8 12.5 45.3 0L160 141.2 160 448c0 17.7 14.3 32 32 32s32-14.3 32-32l0-306.7L329.4 246.6c12.5 12.5 32.8 12.5 45.3 0s12.5-32.8 0-45.3l-160-160z"/>
          </svg>
        </button>
      </div>
    </div>
  );
}

export default ChatWindow;