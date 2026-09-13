import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import LoginPage from './LoginPage'
import './styles.css'

const root = ReactDOM.createRoot(document.getElementById('root')!)
root.render(<React.StrictMode>{window.location.pathname === '/login' ? <LoginPage /> : <App />}</React.StrictMode>)
