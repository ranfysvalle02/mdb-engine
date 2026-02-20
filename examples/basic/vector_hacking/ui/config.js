/**
 * Configuration for Vector Hacking UI
 * 
 * This file configures the API endpoint for the frontend.
 * You can modify this to point to any Vector Hacking API instance.
 */

// API Configuration
// Change this to point to your Vector Hacking API server
window.VECTOR_HACKING_CONFIG = {
    // API base URL (no trailing slash)
    // Examples:
    //   - Local development: 'http://localhost:8000'
    //   - Docker: 'http://api:8000' (when using docker-compose)
    //   - Production: 'https://your-api-server.com'
    API_URL: window.API_URL || 'http://localhost:8000',
    
    // Polling interval in milliseconds (how often to check attack status)
    POLL_INTERVAL: 1000,
    
    // Enable debug logging in console
    DEBUG: false,
};

// Log configuration if debug mode is enabled
if (window.VECTOR_HACKING_CONFIG.DEBUG) {
    console.log('Vector Hacking UI Config:', window.VECTOR_HACKING_CONFIG);
}

