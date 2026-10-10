"""Stealth launch arguments and runtime patches for Playwright Chromium.

Addresses detection vectors beyond JS fingerprinting:
  - Chromium automation flags (--enable-automation, navigator.webdriver)
  - WebRTC local IP leak
  - Headless-mode signals (missing GPU, media codecs)
  - CDP (Chrome DevTools Protocol) runtime detection
  - Consistent window.outerWidth/outerHeight

Usage:
    from stealth import STEALTH_ARGS, stealth_patches

    launch_kwargs['args'] = STEALTH_ARGS
    context = await pw.chromium.launch_persistent_context(...)
    await stealth_patches(context)
"""

# ---------------------------------------------------------------------------
# Chromium launch arguments that reduce automation fingerprint
# ---------------------------------------------------------------------------

STEALTH_ARGS = [
    # Disable automation-related flags
    '--disable-blink-features=AutomationControlled',

    # WebRTC: prevent local IP leak via STUN/TURN
    '--enforce-webrtc-ip-permission-check',
    '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',

    # GPU and rendering: appear as a normal browser with GPU
    '--disable-features=IsolateOrigins,site-per-process',

    # Disable infobars ("Chrome is being controlled by automated software")
    '--disable-infobars',

    # Consistent window size behavior
    '--window-position=0,0',

    # Disable component extensions (password manager popups etc.)
    '--disable-component-extensions-with-background-pages',
    '--disable-default-apps',

    # Don't show first run experience
    '--no-first-run',
    '--no-default-browser-check',

    # Disable background networking that could fingerprint
    '--disable-background-networking',
    '--disable-client-side-phishing-detection',
    '--disable-sync',

    # Media codecs: ensure they exist (headless sometimes lacks them)
    '--autoplay-policy=no-user-gesture-required',
]


# ---------------------------------------------------------------------------
# Runtime patches applied after context creation
# ---------------------------------------------------------------------------

_STEALTH_SCRIPT = r'''
(() => {
  "use strict";

  // ---- CDP Runtime Detection ----
  // Some sites detect automation by checking for the Runtime.evaluate
  // injection marker or by probing Error.prepareStackTrace.
  // Freeze the stack trace API to prevent fingerprinting via error stacks.
  try {
    if (Error.captureStackTrace) {
      const origCST = Error.captureStackTrace;
      Error.captureStackTrace = function(target, constructorOpt) {
        origCST.call(this, target, constructorOpt);
        // Clean automation-related frames from stack
        if (target && target.stack) {
          target.stack = target.stack
            .split('\n')
            .filter(line => !line.includes('__playwright') && !line.includes('Runtime.evaluate'))
            .join('\n');
        }
      };
    }
  } catch {}

  // ---- window.outerWidth/outerHeight ----
  // Headless Chromium sets these to 0; real browsers match innerWidth/innerHeight + chrome.
  try {
    Object.defineProperty(window, 'outerWidth', {
      get: () => window.innerWidth,
      configurable: true,
    });
    Object.defineProperty(window, 'outerHeight', {
      get: () => window.innerHeight + 85, // typical chrome height
      configurable: true,
    });
  } catch {}

  // ---- WebRTC local IP leak (belt-and-suspenders with launch args) ----
  // Override RTCPeerConnection to prevent local IP enumeration
  if (typeof RTCPeerConnection !== 'undefined') {
    const OrigRTC = RTCPeerConnection;
    const origCreateOffer = RTCPeerConnection.prototype.createOffer;
    const origCreateDataChannel = RTCPeerConnection.prototype.createDataChannel;

    // Intercept onicecandidate to strip local IPs
    const origAddEventListener = RTCPeerConnection.prototype.addEventListener;
    RTCPeerConnection.prototype.addEventListener = function(type, listener, ...args) {
      if (type === 'icecandidate') {
        const wrapped = function(event) {
          if (event.candidate && event.candidate.candidate) {
            // Strip mDNS-resolved and local IP candidates
            const c = event.candidate.candidate;
            if (c.includes('.local') || /\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d+\.\d+\b/.test(c)) {
              // Create a synthetic event with null candidate (end-of-candidates signal)
              return;
            }
          }
          listener.call(this, event);
        };
        return origAddEventListener.call(this, type, wrapped, ...args);
      }
      return origAddEventListener.call(this, type, listener, ...args);
    };
  }

  // ---- Notification.permission ----
  // Headless returns 'denied' by default; real browsers return 'default'
  try {
    if (typeof Notification !== 'undefined') {
      Object.defineProperty(Notification, 'permission', {
        get: () => 'default',
        configurable: true,
      });
    }
  } catch {}

  // ---- iframe contentWindow ----
  // Some detectors check if contentWindow is accessible on cross-origin iframes
  // In headless mode this can behave differently. No patch needed if using
  // proper Chromium with --disable-blink-features=AutomationControlled.

  // ---- console.debug marker ----
  // (dev only) Uncomment: console.debug('[stealth] patches loaded');
})();
'''


async def stealth_patches(context):
    """Apply stealth runtime patches to a Playwright BrowserContext.

    Call this AFTER launch_persistent_context but BEFORE navigating to any page.
    """
    await context.add_init_script(_STEALTH_SCRIPT)
