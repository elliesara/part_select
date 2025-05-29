import React, { useState } from "react";
import "./App.css";
import ChatWindow from "./components/ChatWindow";

function App() {

  return (
    <div className="App">
      <div className="heading">
        <img src="/ps-logo.png" alt="PartSelect Logo" className="chat-logo" />
      </div>
        <ChatWindow/>
    </div>
  );
}

export default App;
