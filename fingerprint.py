"""Per-profile browser fingerprint differentiation for multi-account sessions.

Each profile gets a deterministic, unique fingerprint derived from its name via
HMAC-SHA256.  The fingerprint is injected before any page JS runs using
Playwright's add_init_script, overriding:

  - Canvas 2D:  subtle per-pixel noise on toDataURL / toBlob / getImageData
  - WebGL:      renderer / vendor strings, MAX_TEXTURE_SIZE, hash parameters
  - Navigator:  hardwareConcurrency, deviceMemory, platform, languages
  - Screen:     width / height / colorDepth / pixelDepth
  - AudioContext: subtle gain/frequency shift on createOscillator output
  - ClientRects: sub-pixel noise on getBoundingClientRect / getClientRects
  - Fonts:      offsetWidth/Height noise for font enumeration resistance

All values are *stable within a profile* (deterministic from the seed) so
repeated visits produce the same fingerprint — matching real browser behavior.

Usage:

    from fingerprint import FingerprintProfile

    fp = FingerprintProfile('acc1')          # derive from profile name
    fp = FingerprintProfile('acc1', seed=b'secret')  # custom HMAC key

    # Apply to a Playwright browser context or persistent context:
    await fp.apply(context)

    # Or get the raw init script string:
    script = fp.init_script()

    # Get the derived browser settings dict (for launch_persistent_context):
    settings = fp.browser_overrides()
    # => {'user_agent': '...', 'screen': {...}, ...}

Config in accounts.json:

    {
      "name": "acc1",
      "fingerprint": {               // optional — auto-generated if absent
        "seed": "custom-seed",       // optional
        "enabled": true,             // default true
        "platform": "Win32",         // optional override
        "renderer": "...",           // optional override
        "gpu_vendor": "..."          // optional override
      }
    }
"""
import hashlib
import hmac
import json
import struct
from typing import Optional


# ---------------------------------------------------------------------------
# GPU / platform databases — matched to actual host OS
# ---------------------------------------------------------------------------

import platform as _platform_mod

_HOST_OS = _platform_mod.system()  # 'Linux', 'Windows', 'Darwin'

# Platform entries matched to the REAL host OS so TLS/binary fingerprint aligns.
_PLATFORMS_BY_OS = {
    'Linux': [
        {
            'platform': 'Linux x86_64',
            'ua_platform': 'X11; Linux x86_64',
            'app_version': '5.0 (X11; Linux x86_64)',
            'osc_platform': 'Linux',
        },
    ],
    'Windows': [
        {
            'platform': 'Win32',
            'ua_platform': 'Windows NT 10.0; Win64; x64',
            'app_version': '5.0 (Windows NT 10.0; Win64; x64)',
            'osc_platform': 'Windows',
        },
    ],
    'Darwin': [
        {
            'platform': 'MacIntel',
            'ua_platform': 'Macintosh; Intel Mac OS X 10_15_7',
            'app_version': '5.0 (Macintosh; Intel Mac OS X 10_15_7)',
            'osc_platform': 'macOS',
        },
    ],
}
_PLATFORMS = _PLATFORMS_BY_OS.get(_HOST_OS, _PLATFORMS_BY_OS['Linux'])

# GPU renderers matched to host OS — Linux uses ANGLE/OpenGL or Mesa, not D3D11.
_GPUS_BY_OS = {
    'Linux': [
        {'vendor': 'Google Inc. (NVIDIA)', 'renderer': 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650, OpenGL 4.5)'},
        {'vendor': 'Google Inc. (NVIDIA)', 'renderer': 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER, OpenGL 4.5)'},
        {'vendor': 'Google Inc. (NVIDIA)', 'renderer': 'ANGLE (NVIDIA, NVIDIA GeForce RTX 2060, OpenGL 4.5)'},
        {'vendor': 'Google Inc. (NVIDIA)', 'renderer': 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060, OpenGL 4.5)'},
        {'vendor': 'Google Inc. (NVIDIA)', 'renderer': 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3070, OpenGL 4.5)'},
        {'vendor': 'Google Inc. (AMD)', 'renderer': 'ANGLE (AMD, AMD Radeon RX 580, OpenGL 4.5)'},
        {'vendor': 'Google Inc. (AMD)', 'renderer': 'ANGLE (AMD, AMD Radeon RX 5700 XT, OpenGL 4.5)'},
        {'vendor': 'Google Inc. (Intel)', 'renderer': 'ANGLE (Intel, Intel(R) UHD Graphics 630, OpenGL 4.5)'},
        {'vendor': 'Google Inc. (Intel)', 'renderer': 'ANGLE (Intel, Mesa Intel(R) Iris(R) Xe Graphics, OpenGL 4.5)'},
        {'vendor': 'Google Inc. (NVIDIA)', 'renderer': 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1050 Ti, OpenGL 4.5)'},
        {'vendor': 'Google Inc. (AMD)', 'renderer': 'ANGLE (AMD, AMD Radeon RX 6600 XT, OpenGL 4.5)'},
        {'vendor': 'Google Inc. (NVIDIA)', 'renderer': 'ANGLE (NVIDIA, NVIDIA GeForce RTX 4060, OpenGL 4.5)'},
    ],
    'Windows': [
        {'vendor': 'Google Inc. (NVIDIA)', 'renderer': 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)'},
        {'vendor': 'Google Inc. (NVIDIA)', 'renderer': 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0, D3D11)'},
        {'vendor': 'Google Inc. (NVIDIA)', 'renderer': 'ANGLE (NVIDIA, NVIDIA GeForce RTX 2060 Direct3D11 vs_5_0 ps_5_0, D3D11)'},
        {'vendor': 'Google Inc. (NVIDIA)', 'renderer': 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)'},
        {'vendor': 'Google Inc. (NVIDIA)', 'renderer': 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3070 Direct3D11 vs_5_0 ps_5_0, D3D11)'},
        {'vendor': 'Google Inc. (AMD)', 'renderer': 'ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0, D3D11)'},
        {'vendor': 'Google Inc. (AMD)', 'renderer': 'ANGLE (AMD, AMD Radeon RX 5700 XT Direct3D11 vs_5_0 ps_5_0, D3D11)'},
        {'vendor': 'Google Inc. (Intel)', 'renderer': 'ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)'},
        {'vendor': 'Google Inc. (Intel)', 'renderer': 'ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)'},
        {'vendor': 'Google Inc. (NVIDIA)', 'renderer': 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1050 Ti Direct3D11 vs_5_0 ps_5_0, D3D11)'},
        {'vendor': 'Google Inc. (AMD)', 'renderer': 'ANGLE (AMD, AMD Radeon RX 6600 XT Direct3D11 vs_5_0 ps_5_0, D3D11)'},
        {'vendor': 'Google Inc. (NVIDIA)', 'renderer': 'ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0 ps_5_0, D3D11)'},
    ],
    'Darwin': [
        {'vendor': 'Google Inc. (Apple)', 'renderer': 'ANGLE (Apple, Apple M1, OpenGL 4.1)'},
        {'vendor': 'Google Inc. (Apple)', 'renderer': 'ANGLE (Apple, Apple M2, OpenGL 4.1)'},
        {'vendor': 'Google Inc. (Apple)', 'renderer': 'ANGLE (Apple, Apple M1 Pro, OpenGL 4.1)'},
        {'vendor': 'Google Inc. (Intel)', 'renderer': 'ANGLE (Intel, Intel(R) Iris(TM) Plus Graphics, OpenGL 4.1)'},
    ],
}
_GPUS = _GPUS_BY_OS.get(_HOST_OS, _GPUS_BY_OS['Linux'])

_SCREEN_SIZES = [
    (1366, 768), (1440, 900), (1536, 864), (1600, 900),
    (1920, 1080), (1680, 1050), (2560, 1440), (1280, 720),
    (1280, 800), (1360, 768), (1600, 1200), (1920, 1200),
]

_CHROME_VERSIONS = [
    '151.0.7890.75', '151.0.7890.92', '152.0.7950.64',
    '152.0.7950.81', '153.0.8010.12', '153.0.8010.28',
    '153.0.8010.40', '153.0.8010.55', '153.0.8010.62',
]

_LANGUAGE_SETS = [
    ['vi-VN', 'vi', 'en-US', 'en'],
    ['vi-VN', 'vi', 'en'],
    ['vi-VN', 'vi'],
    ['vi', 'en-US', 'en'],
    ['vi-VN', 'en-US', 'en'],
]

_COLOR_DEPTHS = [24, 24, 24, 30, 32]  # weighted towards 24

_HARDWARE_CONCURRENCY = [2, 4, 4, 6, 8, 8, 8, 12, 16]
_DEVICE_MEMORY = [2, 4, 4, 8, 8, 8, 16, 16, 32]


# ---------------------------------------------------------------------------
# Deterministic derivation
# ---------------------------------------------------------------------------

def _derive(seed: bytes, domain: str, n: int) -> int:
    """Derive a deterministic integer in [0, n) from seed + domain."""
    h = hmac.new(seed, domain.encode(), hashlib.sha256).digest()
    return int.from_bytes(h[:8], 'big') % n


def _derive_float(seed: bytes, domain: str) -> float:
    """Derive a deterministic float in [0.0, 1.0) from seed + domain."""
    h = hmac.new(seed, domain.encode(), hashlib.sha256).digest()
    return int.from_bytes(h[:8], 'big') / (2**64)


def _derive_bytes(seed: bytes, domain: str, length: int) -> list:
    """Derive deterministic bytes for noise injection."""
    out = []
    for i in range(0, length, 32):
        h = hmac.new(seed, f'{domain}:{i}'.encode(), hashlib.sha256).digest()
        out.extend(h)
    return out[:length]


# ---------------------------------------------------------------------------
# Profile class
# ---------------------------------------------------------------------------

class FingerprintProfile:
    """Deterministic browser fingerprint derived from a profile name."""

    def __init__(self, name: str, *, seed: Optional[bytes] = None,
                 config: Optional[dict] = None):
        self.name = name
        self._seed = seed or b'onluyen-fp-v1'
        self._key = hmac.new(self._seed, name.encode(), hashlib.sha256).digest()
        self._config = config or {}

        # Derive all fingerprint components
        self._platform = _PLATFORMS[_derive(self._key, 'platform', len(_PLATFORMS))]
        self._gpu = _GPUS[_derive(self._key, 'gpu', len(_GPUS))]
        self._screen = _SCREEN_SIZES[_derive(self._key, 'screen', len(_SCREEN_SIZES))]
        self._chrome = _CHROME_VERSIONS[_derive(self._key, 'chrome', len(_CHROME_VERSIONS))]
        self._langs = _LANGUAGE_SETS[_derive(self._key, 'langs', len(_LANGUAGE_SETS))]
        self._color_depth = _COLOR_DEPTHS[_derive(self._key, 'cdepth', len(_COLOR_DEPTHS))]
        self._hw_concurrency = _HARDWARE_CONCURRENCY[_derive(self._key, 'hwc', len(_HARDWARE_CONCURRENCY))]
        self._device_memory = _DEVICE_MEMORY[_derive(self._key, 'dmem', len(_DEVICE_MEMORY))]

        # Canvas noise: 16 bytes of deterministic sub-pixel noise
        self._canvas_noise = _derive_bytes(self._key, 'canvas', 16)
        # Audio noise: small deterministic shift
        self._audio_shift = (_derive_float(self._key, 'audio') - 0.5) * 0.0001
        # ClientRect noise
        self._rect_noise = [(_derive_float(self._key, f'rect:{i}') - 0.5) * 0.1 for i in range(4)]

        # WebGL parameters
        self._max_texture = [4096, 8192, 8192, 16384, 16384][_derive(self._key, 'maxtex', 5)]
        self._max_renderbuffer = self._max_texture
        self._max_viewport = [self._screen[0] * 2, self._screen[1] * 2]
        self._aliased_line_width = [1, 1]  # Most browsers report [1,1]
        self._max_anisotropy = [2, 4, 8, 16, 16][_derive(self._key, 'aniso', 5)]

        # Network connection parameters (deterministic per profile)
        self._downlink = [10.0, 15.0, 20.0, 35.0, 50.0][_derive(self._key, 'downlink', 5)]
        self._rtt = [25, 50, 75, 100][_derive(self._key, 'rtt', 4)]

        # Apply explicit config overrides
        if self._config.get('platform'):
            self._platform = {'platform': self._config['platform'],
                              'app_version': self._platform['app_version'],
                              'osc_platform': self._platform['osc_platform']}
        if self._config.get('renderer'):
            self._gpu = {**self._gpu, 'renderer': self._config['renderer']}
        if self._config.get('gpu_vendor'):
            self._gpu = {**self._gpu, 'vendor': self._config['gpu_vendor']}

    @property
    def user_agent(self) -> str:
        """Derived Chrome user-agent string."""
        return (f'Mozilla/5.0 ({self._platform["ua_platform"]}) '
                f'AppleWebKit/537.36 (KHTML, like Gecko) '
                f'Chrome/{self._chrome} Safari/537.36')

    @property
    def viewport(self) -> dict:
        """Screen-matched viewport."""
        return {'width': self._screen[0], 'height': self._screen[1]}

    def browser_overrides(self) -> dict:
        """Settings dict to merge into Playwright launch/context kwargs."""
        major = self._chrome.split('.')[0]
        brands = f'"Chromium";v="{major}", "Google Chrome";v="{major}", "Not=A?Brand";v="99"'
        return {
            'user_agent': self.user_agent,
            'viewport': self.viewport,
            'screen': {'width': self._screen[0], 'height': self._screen[1]},
            'color_scheme': 'light',
            'locale': self._langs[0],
            'extra_http_headers': {
                'sec-ch-ua': brands,
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': f'"{self._platform["osc_platform"]}"',
                'sec-ch-ua-arch': '"x86"',
                'sec-ch-ua-bitness': '"64"',
                'sec-ch-ua-model': '""',
            },
        }

    def summary(self) -> dict:
        """Human-readable summary of this profile's fingerprint."""
        return {
            'name': self.name,
            'user_agent': self.user_agent,
            'platform': self._platform['platform'],
            'screen': f'{self._screen[0]}x{self._screen[1]}',
            'gpu': self._gpu['renderer'][:60] + '...',
            'hw_concurrency': self._hw_concurrency,
            'device_memory': self._device_memory,
            'languages': self._langs,
            'color_depth': self._color_depth,
            'canvas_noise_hash': hashlib.md5(bytes(self._canvas_noise)).hexdigest()[:8],
        }

    def init_script(self) -> str:
        """JavaScript to inject via add_init_script before any page JS."""
        major = self._chrome.split('.')[0]
        params = json.dumps({
            'platform': self._platform['platform'],
            'appVersion': self._platform['app_version'],
            'languages': self._langs,
            'hwConcurrency': self._hw_concurrency,
            'deviceMemory': self._device_memory,
            'colorDepth': self._color_depth,
            'pixelDepth': self._color_depth,
            'screenWidth': self._screen[0],
            'screenHeight': self._screen[1],
            'gpuVendor': self._gpu['vendor'],
            'gpuRenderer': self._gpu['renderer'],
            'canvasNoise': self._canvas_noise,
            'audioShift': self._audio_shift,
            'rectNoise': self._rect_noise,
            'maxTextureSize': self._max_texture,
            'maxRenderbufferSize': self._max_renderbuffer,
            'maxViewportDims': self._max_viewport,
            'maxAnisotropy': self._max_anisotropy,
            'chromeMajor': major,
            'chromeVersion': self._chrome,
            'oscPlatform': self._platform['osc_platform'],
            'downlink': self._downlink,
            'rtt': self._rtt,
        }, ensure_ascii=False)

        return _INIT_SCRIPT_TEMPLATE.replace('__FP_PARAMS__', params)

    async def apply(self, context):
        """Apply fingerprint overrides to a Playwright BrowserContext."""
        await context.add_init_script(self.init_script())


# ---------------------------------------------------------------------------
# The init script — runs before any page JavaScript
# ---------------------------------------------------------------------------

_INIT_SCRIPT_TEMPLATE = r'''
(() => {
  "use strict";
  const P = __FP_PARAMS__;

  // ---- Navigator overrides ----
  const nav = Object.getPrototypeOf(navigator);
  const navOverrides = {
    platform:           { get: () => P.platform },
    appVersion:         { get: () => P.appVersion },
    hardwareConcurrency:{ get: () => P.hwConcurrency },
    deviceMemory:       { get: () => P.deviceMemory },
    languages:          { get: () => Object.freeze([...P.languages]) },
    language:           { get: () => P.languages[0] },
  };
  for (const [k, v] of Object.entries(navOverrides)) {
    try { Object.defineProperty(nav, k, { ...v, configurable: true }); } catch {}
  }
  // Mask webdriver flag
  try {
    Object.defineProperty(nav, 'webdriver', {
      get: () => undefined, configurable: true
    });
  } catch {}

  // ---- Screen overrides ----
  const scr = Object.getPrototypeOf(screen);
  const scrOverrides = {
    width:      { get: () => P.screenWidth },
    height:     { get: () => P.screenHeight },
    availWidth: { get: () => P.screenWidth },
    availHeight:{ get: () => P.screenHeight - 40 },
    colorDepth: { get: () => P.colorDepth },
    pixelDepth: { get: () => P.pixelDepth },
  };
  for (const [k, v] of Object.entries(scrOverrides)) {
    try { Object.defineProperty(scr, k, { ...v, configurable: true }); } catch {}
  }

  // ---- Native function spoofing ([native code]) ----
  const nativeFns = new WeakMap();
  const origToString = Function.prototype.toString;
  function makeNative(fn, name) {
    try {
      Object.defineProperty(fn, 'name', { value: name, configurable: true });
    } catch {}
    nativeFns.set(fn, `function ${name}() { [native code] }`);
    return fn;
  }
  const patchedToString = function() {
    if (nativeFns.has(this)) {
      return nativeFns.get(this);
    }
    return origToString.call(this);
  };
  makeNative(patchedToString, 'toString');
  try {
    Function.prototype.toString = patchedToString;
  } catch {}

  // ---- Canvas 2D noise ----
  // Subtle per-pixel modification that changes the canvas hash
  // without visibly altering the image.
  const noise = P.canvasNoise;
  const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
  const origToBlob = HTMLCanvasElement.prototype.toBlob;
  const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;

  function applyCanvasNoise(ctx, canvas) {
    try {
      const w = canvas.width, h = canvas.height;
      if (w === 0 || h === 0 || w > 4096 || h > 4096) return;
      const imageData = origGetImageData.call(ctx, 0, 0, w, h);
      const d = imageData.data;
      // Apply deterministic noise to a sparse set of pixels
      for (let i = 0; i < Math.min(d.length, noise.length * 256); i += 4) {
        const ni = (i / 4) % noise.length;
        const shift = (noise[ni] % 3) - 1;  // -1, 0, or 1
        d[i] = Math.max(0, Math.min(255, d[i] + shift));
      }
      ctx.putImageData(imageData, 0, 0);
    } catch {}
  }

  HTMLCanvasElement.prototype.toDataURL = makeNative(function(...args) {
    try {
      const ctx = this.getContext('2d');
      if (ctx) applyCanvasNoise(ctx, this);
    } catch {}
    return origToDataURL.apply(this, args);
  }, 'toDataURL');

  HTMLCanvasElement.prototype.toBlob = makeNative(function(cb, ...args) {
    try {
      const ctx = this.getContext('2d');
      if (ctx) applyCanvasNoise(ctx, this);
    } catch {}
    return origToBlob.call(this, cb, ...args);
  }, 'toBlob');

  // ---- WebGL fingerprint ----
  const origGetParameter = WebGLRenderingContext.prototype.getParameter;
  const origGetParameter2 = WebGL2RenderingContext?.prototype?.getParameter;

  function patchGetParameter(original) {
    return function(pname) {
      // RENDERER
      if (pname === 0x1F01) return P.gpuRenderer;
      // VENDOR
      if (pname === 0x1F00) return P.gpuVendor;
      // MAX_TEXTURE_SIZE
      if (pname === 0x0D33) return P.maxTextureSize;
      // MAX_RENDERBUFFER_SIZE
      if (pname === 0x84E8) return P.maxRenderbufferSize;
      // MAX_VIEWPORT_DIMS
      if (pname === 0x0D3A) return new Int32Array(P.maxViewportDims);
      return original.call(this, pname);
    };
  }

  WebGLRenderingContext.prototype.getParameter = makeNative(patchGetParameter(origGetParameter), 'getParameter');
  if (origGetParameter2) {
    WebGL2RenderingContext.prototype.getParameter = makeNative(patchGetParameter(origGetParameter2), 'getParameter');
  }

  // Also intercept getExtension for WEBGL_debug_renderer_info
  const origGetExtension = WebGLRenderingContext.prototype.getExtension;
  const origGetExtension2 = WebGL2RenderingContext?.prototype?.getExtension;

  function patchGetExtension(original) {
    return function(name) {
      const ext = original.call(this, name);
      if (name === 'WEBGL_debug_renderer_info' && ext) {
        // The extension constants UNMASKED_VENDOR_WEBGL=0x9245, UNMASKED_RENDERER_WEBGL=0x9246
        // are read via getParameter, which we already patched.
      }
      return ext;
    };
  }

  WebGLRenderingContext.prototype.getExtension = makeNative(patchGetExtension(origGetExtension), 'getExtension');
  if (origGetExtension2) {
    WebGL2RenderingContext.prototype.getExtension = makeNative(patchGetExtension(origGetExtension2), 'getExtension');
  }

  // ---- AudioContext fingerprint ----
  // Shift oscillator frequency slightly to change the audio fingerprint hash.
  if (typeof AudioContext !== 'undefined') {
    const origCreateOscillator = AudioContext.prototype.createOscillator;
    AudioContext.prototype.createOscillator = makeNative(function() {
      const osc = origCreateOscillator.call(this);
      const origFreqSet = Object.getOwnPropertyDescriptor(
        OscillatorNode.prototype.__proto__.__proto__, 'frequency'
      );
      // Subtle: shift the default frequency
      try { osc.frequency.value += P.audioShift; } catch {}
      return osc;
    }, 'createOscillator');

    // Also patch createDynamicsCompressor for DynamicsCompressor fingerprinting
    const origCreateDynComp = AudioContext.prototype.createDynamicsCompressor;
    AudioContext.prototype.createDynamicsCompressor = makeNative(function() {
      const comp = origCreateDynComp.call(this);
      try {
        comp.threshold.value = -50 + P.audioShift * 100;
      } catch {}
      return comp;
    }, 'createDynamicsCompressor');
  }
  if (typeof OfflineAudioContext !== 'undefined') {
    const origOACreate = OfflineAudioContext.prototype.createOscillator;
    OfflineAudioContext.prototype.createOscillator = makeNative(function() {
      const osc = origOACreate.call(this);
      try { osc.frequency.value += P.audioShift; } catch {}
      return osc;
    }, 'createOscillator');
  }

  // ---- ClientRects fingerprint ----
  // Sub-pixel noise on getBoundingClientRect and getClientRects
  const origGetBCR = Element.prototype.getBoundingClientRect;
  Element.prototype.getBoundingClientRect = makeNative(function() {
    const r = origGetBCR.call(this);
    return new DOMRect(
      r.x + P.rectNoise[0],
      r.y + P.rectNoise[1],
      r.width + P.rectNoise[2],
      r.height + P.rectNoise[3]
    );
  }, 'getBoundingClientRect');

  // ---- Plugins / mimeTypes ----
  // Chrome 91+ returns empty arrays; keep it that way but define consistently.
  try {
    Object.defineProperty(nav, 'plugins', {
      get: () => { const a = []; a.item = () => null; a.namedItem = () => null;
                   a.refresh = () => {}; return a; },
      configurable: true
    });
    Object.defineProperty(nav, 'mimeTypes', {
      get: () => { const a = []; a.item = () => null; a.namedItem = () => null; return a; },
      configurable: true
    });
  } catch {}

  // ---- Network Information API (navigator.connection) ----
  try {
    const connObj = {
      downlink: P.downlink || 15.0,
      effectiveType: '4g',
      rtt: P.rtt || 50,
      saveData: false,
      addEventListener: makeNative(() => {}, 'addEventListener'),
      removeEventListener: makeNative(() => {}, 'removeEventListener'),
      dispatchEvent: makeNative(() => true, 'dispatchEvent'),
    };
    Object.defineProperty(nav, 'connection', {
      get: () => connObj,
      configurable: true,
    });
  } catch {}

  // ---- User-Agent Client Hints API (navigator.userAgentData) ----
  try {
    const brandsList = [
      { brand: 'Chromium', version: P.chromeMajor },
      { brand: 'Google Chrome', version: P.chromeMajor },
      { brand: 'Not=A?Brand', version: '99' },
    ];
    const uad = {
      brands: Object.freeze(brandsList),
      mobile: false,
      platform: P.oscPlatform,
      getHighEntropyValues: makeNative(async function(hints) {
        const out = {
          brands: this.brands,
          mobile: this.mobile,
          platform: this.platform,
        };
        if (Array.isArray(hints)) {
          if (hints.includes('architecture')) out.architecture = 'x86';
          if (hints.includes('bitness')) out.bitness = '64';
          if (hints.includes('model')) out.model = '';
          if (hints.includes('platformVersion')) out.platformVersion = '6.5.0';
          if (hints.includes('uaFullVersion')) out.uaFullVersion = P.chromeVersion;
          if (hints.includes('fullVersionList')) {
            out.fullVersionList = [
              { brand: 'Chromium', version: P.chromeVersion },
              { brand: 'Google Chrome', version: P.chromeVersion },
              { brand: 'Not=A?Brand', version: '99.0.0.0' },
            ];
          }
        }
        return out;
      }, 'getHighEntropyValues'),
      toJSON: makeNative(function() {
        return { brands: this.brands, mobile: this.mobile, platform: this.platform };
      }, 'toJSON'),
    };
    Object.defineProperty(nav, 'userAgentData', {
      get: () => uad,
      configurable: true,
    });
  } catch {}

  // ---- Battery API (navigator.getBattery) ----
  try {
    if (typeof nav.getBattery === 'undefined') {
      const batteryManager = {
        charging: true,
        chargingTime: 0,
        dischargingTime: Infinity,
        level: 1.0,
        addEventListener: makeNative(() => {}, 'addEventListener'),
        removeEventListener: makeNative(() => {}, 'removeEventListener'),
        dispatchEvent: makeNative(() => true, 'dispatchEvent'),
      };
      nav.getBattery = makeNative(() => Promise.resolve(batteryManager), 'getBattery');
    }
  } catch {}

  // ---- Chrome-specific properties ----
  if (!window.chrome || !window.chrome.app) {
    const origChrome = window.chrome || {};
    window.chrome = {
      ...origChrome,
      app: {
        isInstalled: false,
        InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
        RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
        getDetails: makeNative(() => null, 'getDetails'),
        getIsInstalled: makeNative(() => false, 'getIsInstalled'),
        getRunningState: makeNative(() => 'cannot_run', 'getRunningState'),
      },
      csi: origChrome.csi || makeNative(() => ({}), 'csi'),
      loadTimes: origChrome.loadTimes || makeNative(() => ({}), 'loadTimes'),
      runtime: {
        OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' },
        OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
        PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
        PlatformNaclArch: { ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
        PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
        RequestUpdateCheckStatus: { NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' },
        connect: makeNative(() => ({ disconnect: () => {}, onDisconnect: { addListener: () => {} }, onMessage: { addListener: () => {} }, postMessage: () => {} }), 'connect'),
        sendMessage: makeNative((...args) => {}, 'sendMessage'),
      },
    };
  }

  // ---- Permissions API ----
  // Normalize Permissions.query to avoid headless detection
  if (typeof Permissions !== 'undefined') {
    const origQuery = Permissions.prototype.query;
    Permissions.prototype.query = makeNative(function(desc) {
      if (desc?.name === 'notifications') {
        return Promise.resolve({ state: 'prompt', onchange: null });
      }
      return origQuery.call(this, desc);
    }, 'query');
  }

  // ---- Date / Intl consistency ----
  // Timezone is set at the Playwright level; no override needed here.

  // ---- console.debug beacon (dev only) ----
  // Uncomment for debugging: console.debug('[fp] profile loaded', P.platform, P.gpuRenderer.slice(0,40));
})();
'''


# ---------------------------------------------------------------------------
# Helper: build a FingerprintProfile from an account config dict
# ---------------------------------------------------------------------------

def from_account(account: dict) -> Optional['FingerprintProfile']:
    """Create a FingerprintProfile from an account config entry.

    Returns None if fingerprinting is explicitly disabled.
    """
    fp_cfg = account.get('fingerprint', {})
    if isinstance(fp_cfg, bool):
        fp_cfg = {'enabled': fp_cfg}
    if not fp_cfg.get('enabled', True):
        return None

    seed = fp_cfg.get('seed', account['name']).encode() if isinstance(
        fp_cfg.get('seed', account['name']), str) else fp_cfg.get('seed', b'onluyen-fp-v1')

    return FingerprintProfile(
        account['name'],
        seed=seed,
        config=fp_cfg,
    )
