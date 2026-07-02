from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def run_cast_player_script(
    script_body: str,
    *,
    search: str = "?cast=/casts/demo.cast&title=Demo",
    parent_search: str | None = None,
) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")

    script = (
        r"""
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const html = fs.readFileSync('website/static/cast-player.html', 'utf8');
const scripts = [...html.matchAll(/<script(?:\s+src="([^"]+)")?\s*>([\s\S]*?)<\/script>/g)];
const elements = new Map();
const timeoutCalls = [];

function element(id) {
  if (!elements.has(id)) {
    const item = {
      id,
      children: [],
      className: '',
      textContent: '',
      innerHTML: '',
      scrollTop: 0,
      scrollLeft: 0,
      scrollHeight: 0,
      clientHeight: 0,
      offsetTop: 0,
      offsetHeight: 0,
      lastScrollTo: null,
      disabled: false,
      hidden: false,
      style: {
        properties: {},
        setProperty(name, value) {
          this.properties[name] = value;
        },
      },
      dataset: {},
      appendChild(child) {
        this.children.push(child);
      },
      scrollTo(options) {
        this.lastScrollTo = options;
        if (typeof options === 'object') {
          this.scrollTop = Number(options.top || 0);
          this.scrollLeft = Number(options.left || 0);
        }
      },
      querySelectorAll(selector) {
        if (selector === '.section-marker') {
          return this.children.filter((child) => (
            String(child.className || '').split(/\s+/).includes('section-marker')
          ));
        }
        return [];
      },
      querySelector(selector) {
        if (selector === '.narration-word.current') {
          return this.children.find((child) => (
            String(child.className || '').split(/\s+/).includes('narration-word') &&
            String(child.className || '').split(/\s+/).includes('current')
          )) || null;
        }
        return null;
      },
      classList: {
        add(...names) {
          const current = new Set(String(item.className || '').split(/\s+/).filter(Boolean));
          names.forEach((name) => current.add(name));
          item.className = [...current].join(' ');
        },
        remove(...names) {
          const removeNames = new Set(names);
          item.className = String(item.className || '')
            .split(/\s+/)
            .filter((name) => name && !removeNames.has(name))
            .join(' ');
        },
      },
      addEventListener() {},
      setAttribute() {},
      removeAttribute(name) {
        if (name.startsWith('data-')) {
          const key = name
            .slice(5)
            .replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase());
          delete this.dataset[key];
        }
      },
    };
    elements.set(id, item);
  }
  return elements.get(id);
}

const context = {
  URLSearchParams,
  window: { location: { search: __SEARCH_JSON__ } },
  document: {
    title: '',
    getElementById: element,
    createElement: () => element(`created-${elements.size}`),
    addEventListener() {},
  },
  fetch: () => new Promise(() => {}),
  Audio: function () {
    return {
      addEventListener() {},
      pause() {},
      play() { return Promise.resolve(); },
      currentTime: 0,
      duration: 0,
      muted: false,
    };
  },
  performance: { now: () => 0 },
  setTimeout: (callback, ms) => {
    timeoutCalls.push({callback, ms});
    return timeoutCalls.length;
  },
  clearTimeout() {},
  setInterval: () => 0,
  clearInterval() {},
  __timeoutCalls: timeoutCalls,
  console,
};
if (__PARENT_SEARCH_JSON__ !== null) {
  context.window.parent = {location: {search: __PARENT_SEARCH_JSON__}};
} else {
  context.window.parent = context.window;
}
context.window.document = context.document;

vm.createContext(context);
for (const script of scripts) {
  const source = script[1]
    ? fs.readFileSync(path.join('website/static', script[1]), 'utf8')
    : script[2];
  vm.runInContext(source, context);
}
""".replace(
            "__SEARCH_JSON__", repr(search)
        ).replace(
            "__PARENT_SEARCH_JSON__",
            "null" if parent_search is None else repr(parent_search),
        )
        + script_body
    )

    return subprocess.run(
        [node, "-"],
        cwd=REPO_ROOT,
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )


def test_cast_player_core_advances_audio_boundary_when_timer_fires_early() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")

    script = r"""
const {createCastAudioTimeline} = require('./website/static/cast-player-core.js');
const timeline = createCastAudioTimeline([
  {
    presentationStart: 0,
    presentationEnd: 10,
    audioStart: 0,
    audioEnd: 10,
  },
  {
    presentationStart: 10,
    presentationEnd: 20,
    audioStart: 10,
    audioEnd: 20,
  },
]);

const first = timeline.segmentForPresentation(9.999);
const advanced = timeline.advanceBoundary(9.999, first);
const next = timeline.segmentForPresentation(advanced);
if (
  advanced !== 10 ||
  next?.presentationStart !== 10 ||
  timeline.audioTimeForPresentation(advanced) !== 10
) {
  console.error(JSON.stringify({advanced, next}));
  process.exit(1);
}
"""
    result = subprocess.run(
        [node, "-"],
        cwd=REPO_ROOT,
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_embed_builds_shared_player_url() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")

    script = r"""
const {buildCastPlayerUrl} = require('./website/static/cast-player-embed.js');
const url = buildCastPlayerUrl({
  player: '/cast-player.html',
  src: '/casts/demo.retimed.cast',
  title: 'Demo Build',
  audio: '/audio/casts/demo.mp3',
  audioMeta: '/audio/casts/demo.json',
  intro: 'Intro text from narration.',
  introSegment: 'overview',
});
if (
  url !== '/cast-player.html?cast=%2Fcasts%2Fdemo.retimed.cast&title=Demo+Build&audio=%2Faudio%2Fcasts%2Fdemo.mp3&audioMeta=%2Faudio%2Fcasts%2Fdemo.json&intro=Intro+text+from+narration.&introSegment=overview'
) {
  console.error(url);
  process.exit(1);
}
"""
    result = subprocess.run(
        [node, "-"],
        cwd=REPO_ROOT,
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_embed_custom_element_renders_iframe() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")

    script = r"""
const fs = require('fs');
const vm = require('vm');

class FakeHTMLElement {
  constructor() {
    this.attributes = new Map();
    this.children = [];
    this.textContent = '';
    this.isConnected = true;
  }

  getAttribute(name) {
    return this.attributes.has(name) ? this.attributes.get(name) : null;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }
}

const defined = new Map();
const context = {
  URLSearchParams,
  HTMLElement: FakeHTMLElement,
  customElements: {
    define(name, klass) {
      defined.set(name, klass);
    },
    get(name) {
      return defined.get(name);
    },
  },
  document: {
    createElement(tagName) {
      return {
        tagName,
        allow: '',
        allowFullscreen: false,
        loading: '',
        src: '',
        title: '',
      };
    },
  },
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync('website/static/cast-player-embed.js', 'utf8'), context);
const Element = context.customElements.get('cast-player-embed');
const element = new Element();
element.setAttribute('title', 'Demo Build');
element.setAttribute('src', '/casts/demo.retimed.cast');
element.setAttribute('audio', '/audio/casts/demo.mp3');
element.setAttribute('audio-meta', '/audio/casts/demo.json');
element.setAttribute('intro-segment', 'overview');
element.setAttribute('player', '/cast-player.html');
element.connectedCallback();
const iframe = element.children[0];
if (
  element.children.length !== 1 ||
  iframe.tagName !== 'iframe' ||
  iframe.title !== 'Demo Build' ||
  iframe.loading !== 'lazy' ||
  iframe.allow !== 'autoplay' ||
  iframe.allowFullscreen !== true ||
  iframe.src !== '/cast-player.html?cast=%2Fcasts%2Fdemo.retimed.cast&title=Demo+Build&audio=%2Faudio%2Fcasts%2Fdemo.mp3&audioMeta=%2Faudio%2Fcasts%2Fdemo.json&introSegment=overview'
) {
  console.error(JSON.stringify({children: element.children.length, iframe}));
  process.exit(1);
}
"""
    result = subprocess.run(
        [node, "-"],
        cwd=REPO_ROOT,
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_carriage_return_overwrites_spinner_frames() -> None:
    result = run_cast_player_script(
        r"""
vm.runInContext(
  'resetTerminalBuffer(); appendChunk("\\r| building installation bundle\\r/ building installation bundle\\r- building installation bundle\\r\\\\ building installation bundle\\rbuilding installation bundle... done\\r\\n");',
  context,
);

const terminalHtml = element('terminal').innerHTML;
if (terminalHtml !== 'building installation bundle... done\n') {
  console.error(JSON.stringify({terminalHtml}));
  process.exit(1);
}
"""
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_playback_flash_holds_then_fades() -> None:
    result = run_cast_player_script(
        r"""
vm.runInContext(`
showPlaybackFlash('pause');
const flash = document.getElementById('playback-flash');
if (flash.dataset.visible !== 'true' || flash.dataset.fading !== 'false') {
  console.error(JSON.stringify({dataset: flash.dataset}));
  process.exit(1);
}
if (!flash.innerHTML.includes('M9 5v14')) {
  console.error(JSON.stringify({innerHTML: flash.innerHTML}));
  process.exit(1);
}
const timerDurations = __timeoutCalls.slice(-2).map((call) => call.ms);
if (JSON.stringify(timerDurations) !== JSON.stringify([100, 700])) {
  console.error(JSON.stringify({timerDurations}));
  process.exit(1);
}
__timeoutCalls.at(-2).callback();
if (flash.dataset.visible !== 'false' || flash.dataset.fading !== 'true') {
  console.error(JSON.stringify({datasetAfterFadeStart: flash.dataset}));
  process.exit(1);
}
__timeoutCalls.at(-1).callback();
if ('visible' in flash.dataset || 'fading' in flash.dataset) {
  console.error(JSON.stringify({datasetAfterCleanup: flash.dataset}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_narration_starts_at_retimed_caption_times() -> None:
    result = run_cast_player_script(
        r"""
vm.runInContext(`
narrationSegments = [
  {id: 'first', offset: 0, duration: 10, heading: 'First', text: 'First'},
  {id: 'second', offset: 10, duration: 4, heading: 'Second', text: 'Second'},
];
buildPresentationEvents([
  {time: 2, data: "\\u001b[36;1m# First\\u001b[0m\\r\\n\\r\\n"},
  {time: 3, data: "\\u001b[36;1m# Second\\u001b[0m\\r\\n\\r\\n"},
]);
const starts = audioPlaybackSegments.map((segment) => segment.presentationStart);
if (JSON.stringify(starts) !== JSON.stringify([2, 3])) {
  console.error(JSON.stringify({starts}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_narration_starts_at_typed_caption_times() -> None:
    result = run_cast_player_script(
        r"""
vm.runInContext(`
narrationSegments = [
  {id: 'first', offset: 0, duration: 10, heading: 'First', text: 'First'},
  {id: 'second', offset: 10, duration: 4, heading: 'Second', text: 'Second'},
];
buildPresentationEvents([
  {time: 2, data: "\\u001b[36;1m"},
  {time: 2.01, data: "#"},
  {time: 2.02, data: " First"},
  {time: 3, data: "\\u001b[36;1m"},
  {time: 3.01, data: "#"},
  {time: 3.02, data: " Second"},
]);
const starts = audioPlaybackSegments.map((segment) => segment.presentationStart);
if (JSON.stringify(starts) !== JSON.stringify([2, 3])) {
  console.error(JSON.stringify({starts}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_scrolls_narration_to_current_word() -> None:
    result = run_cast_player_script(
        r"""
vm.runInContext(`
const container = document.getElementById('narration');
container.clientHeight = 40;
container.scrollHeight = 100;
container.scrollTop = 0;
container.scrollLeft = 15;
container.children = [
  {className: 'narration-word current', offsetTop: 70, offsetHeight: 12},
];
updateNarrationScroll();
if (container.scrollTop !== 60) {
  console.error(JSON.stringify({scrollTop: container.scrollTop}));
  process.exit(1);
}
if (container.scrollLeft !== 0) {
  console.error(JSON.stringify({scrollLeft: container.scrollLeft}));
  process.exit(1);
}
container.scrollTop = 0;
container.scrollLeft = 21;
container.children = [
  {className: 'narration-word current', offsetTop: 22, offsetHeight: 12},
];
updateNarrationScroll();
if (container.scrollTop !== 20) {
  console.error(JSON.stringify({lineStepScrollTop: container.scrollTop}));
  process.exit(1);
}
if (container.scrollLeft !== 0) {
  console.error(JSON.stringify({lineStepScrollLeft: container.scrollLeft}));
  process.exit(1);
}
container.scrollHeight = 40;
container.scrollTop = 12;
container.scrollLeft = 8;
updateNarrationScroll();
if (container.scrollTop !== 0) {
  console.error(JSON.stringify({resetScrollTop: container.scrollTop}));
  process.exit(1);
}
if (container.scrollLeft !== 0) {
  console.error(JSON.stringify({resetScrollLeft: container.scrollLeft}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_animates_narration_line_scroll_up() -> None:
    result = run_cast_player_script(
        r"""
vm.runInContext(`
let now = 1000;
const animationCallbacks = [];
performance.now = () => now;
window.requestAnimationFrame = (callback) => {
  animationCallbacks.push(callback);
  return animationCallbacks.length;
};
window.cancelAnimationFrame = () => {};
const container = document.getElementById('narration');
container.clientHeight = 40;
container.scrollHeight = 100;
container.scrollTop = 0;
container.scrollLeft = 9;
container.children = [
  {className: 'narration-word current', offsetTop: 70, offsetHeight: 12},
];
updateNarrationScroll({animate: true});
if (animationCallbacks.length !== 1) {
  console.error(JSON.stringify({animationCallbacks: animationCallbacks.length}));
  process.exit(1);
}
now += 160;
animationCallbacks.shift()(now);
if (!(container.scrollTop > 0 && container.scrollTop < 60)) {
  console.error(JSON.stringify({midScrollTop: container.scrollTop}));
  process.exit(1);
}
now += 160;
animationCallbacks.shift()(now);
if (container.scrollTop !== 60) {
  console.error(JSON.stringify({finalScrollTop: container.scrollTop}));
  process.exit(1);
}
if (container.scrollLeft !== 0) {
  console.error(JSON.stringify({scrollLeft: container.scrollLeft}));
  process.exit(1);
}

container.scrollTop = 80;
container.children = [
  {className: 'narration-word current', offsetTop: 22, offsetHeight: 12},
];
updateNarrationScroll({animate: true});
if (animationCallbacks.length) {
  console.error(JSON.stringify({unexpectedAnimationCallbacks: animationCallbacks.length}));
  process.exit(1);
}
if (container.scrollTop !== 20) {
  console.error(JSON.stringify({scrollTop: container.scrollTop}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_keeps_narration_scroll_during_timestamp_gap() -> None:
    result = run_cast_player_script(
        r"""
vm.runInContext(`
const container = document.getElementById('narration');
container.clientHeight = 40;
container.scrollHeight = 100;
container.scrollTop = 40;
container.scrollLeft = 12;
container.children = [
  {className: 'narration-word past', offsetTop: 20, offsetHeight: 12},
  {className: 'narration-word future', offsetTop: 60, offsetHeight: 12},
];
updateNarrationScroll({animate: true});
if (container.scrollTop !== 40) {
  console.error(JSON.stringify({scrollTop: container.scrollTop}));
  process.exit(1);
}
if (container.scrollLeft !== 0) {
  console.error(JSON.stringify({scrollLeft: container.scrollLeft}));
  process.exit(1);
}

updateNarrationScroll({animate: false});
if (container.scrollTop !== 0) {
  console.error(JSON.stringify({nonAnimatedScrollTop: container.scrollTop}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_gives_zero_duration_timestamp_words_visible_span() -> None:
    result = run_cast_player_script(
        r"""
vm.runInContext(`
const spans = alignTimestampWords('Plugins. Reploy records.', [
  {word: 'Plugins', start: 5.0, end: 5.4},
  {word: 'Reploy', start: 6.44, end: 6.44},
  {word: 'records', start: 6.44, end: 6.84},
]);
const reploy = spans[1];
const records = spans[2];
if (reploy.start !== 6.44 || reploy.end <= reploy.start) {
  console.error(JSON.stringify({reploy}));
  process.exit(1);
}
if (reploy.end > 6.64) {
  console.error(JSON.stringify({reploy}));
  process.exit(1);
}
if (records.start < reploy.end) {
  console.error(JSON.stringify({reploy, records}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_default_checkpoints_require_guide() -> None:
    result = run_cast_player_script(
        r"""
vm.runInContext(`
narrationSegments = [
  {id: 'first', offset: 0, duration: 3, heading: 'First', text: 'First', guide: {commands: ['echo first']}},
  {id: 'second', offset: 3, duration: 3, heading: 'Second', text: 'Second', guide: null},
  {id: 'third', offset: 6, duration: 3, heading: 'Third', text: 'Third', guide: {commands: ['echo third']}},
];
buildPresentationEvents([
  {time: 1, data: "\\u001b[36;1m# First\\u001b[0m\\r\\n\\r\\n"},
  {time: 5, data: "\\u001b[36;1m# Second\\u001b[0m\\r\\n\\r\\n"},
  {time: 9, data: "\\u001b[36;1m# Third\\u001b[0m\\r\\n\\r\\n"},
]);
const points = guidedPausePoints.map((point) => ({
  time: point.time,
  heading: point.heading,
  debugOnly: Boolean(point.debugOnly),
}));
if (JSON.stringify(points) !== JSON.stringify([
  {time: 5, heading: 'First', debugOnly: false},
])) {
  console.error(JSON.stringify({points}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_uses_segment_pause_after_instead_of_default_pause() -> None:
    result = run_cast_player_script(
        r"""
vm.runInContext(`
introSeconds = 3;
narrationSegments = [
  {id: 'intro', offset: 0, duration: 3, heading: 'Intro', text: 'Intro', pauseAfter: 0},
  {id: 'first', offset: 3, duration: 2, heading: 'First', text: 'First', pauseAfter: 0},
];
let shifted = buildPresentationEvents([
  {time: 1, data: "\\u001b[36;1m# First\\u001b[0m\\r\\n\\r\\n"},
]);
if (shifted[0].time !== 4) {
  console.error(JSON.stringify({withoutPause: shifted[0].time}));
  process.exit(1);
}
narrationSegments[0].pauseAfter = 1.5;
shifted = buildPresentationEvents([
  {time: 1, data: "\\u001b[36;1m# First\\u001b[0m\\r\\n\\r\\n"},
]);
if (shifted[0].time !== 5.5) {
  console.error(JSON.stringify({withPause: shifted[0].time}));
  process.exit(1);
}
`, context);
""",
        search="?cast=/casts/demo.cast&title=Demo&introSegment=intro",
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_renders_ansi_256_color_foreground() -> None:
    result = run_cast_player_script(
        r"""
vm.runInContext(`
appendChunk("\\u001b[38;5;117m[STAGING : arbiter]\\u001b[0m ok");
const html = document.getElementById('terminal').innerHTML;
if (!html.includes('style="color: #87d7ff"')) {
  console.error(JSON.stringify({html}));
  process.exit(1);
}
if (!html.includes('[STAGING : arbiter]')) {
  console.error(JSON.stringify({html}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_handles_ansi_clear_screen() -> None:
    result = run_cast_player_script(
        r"""
vm.runInContext(`
appendChunk("before\\n");
appendChunk("\\u001b[H\\u001b[2Jafter");
const html = document.getElementById('terminal').innerHTML;
if (html.includes('before') || !html.includes('after')) {
  console.error(JSON.stringify({html}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_debug_checkpoints_include_non_guide_beats() -> None:
    result = run_cast_player_script(
        r"""
vm.runInContext(`
narrationSegments = [
  {id: 'first', offset: 0, duration: 3, heading: 'First', text: 'First', guide: {commands: ['echo first']}},
  {id: 'second', offset: 3, duration: 3, heading: 'Second', text: 'Second', guide: null},
  {id: 'third', offset: 6, duration: 3, heading: 'Third', text: 'Third', guide: {commands: ['echo third']}},
];
buildPresentationEvents([
  {time: 1, data: "\\u001b[36;1m# First\\u001b[0m\\r\\n\\r\\n"},
  {time: 5, data: "\\u001b[36;1m# Second\\u001b[0m\\r\\n\\r\\n"},
  {time: 9, data: "\\u001b[36;1m# Third\\u001b[0m\\r\\n\\r\\n"},
]);
const points = guidedPausePoints.map((point) => ({
  time: point.time,
  heading: point.heading,
  debugOnly: Boolean(point.debugOnly),
}));
if (JSON.stringify(points) !== JSON.stringify([
  {time: 5, heading: 'First', debugOnly: false},
  {time: 9, heading: 'Second', debugOnly: true},
])) {
  console.error(JSON.stringify({points}));
  process.exit(1);
}
`, context);
""",
        search="?cast=/casts/demo.cast&title=Demo&debug=1",
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_section_markers_use_valid_percent_left() -> None:
    result = run_cast_player_script(
        r"""
vm.runInContext(`
totalSeconds = 100;
guidedPausePoints = [
  {time: 25, heading: 'Quarter', debugOnly: false},
];
renderSectionMarkers();
const marker = document.getElementById('section-markers').children[0];
if (!marker) {
  console.error('missing marker');
  process.exit(1);
}
if (marker.style.left !== '25%') {
  console.error(JSON.stringify({left: marker.style.left}));
  process.exit(1);
}
if (marker.style.properties?.['--thumb-offset'] === undefined) {
  console.error(JSON.stringify({style: marker.style}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_debug_checkpoints_follow_parent_page_query() -> None:
    result = run_cast_player_script(
        r"""
vm.runInContext(`
narrationSegments = [
  {id: 'first', offset: 0, duration: 3, heading: 'First', text: 'First', guide: {commands: ['echo first']}},
  {id: 'second', offset: 3, duration: 3, heading: 'Second', text: 'Second', guide: null},
  {id: 'third', offset: 6, duration: 3, heading: 'Third', text: 'Third', guide: {commands: ['echo third']}},
];
buildPresentationEvents([
  {time: 1, data: "\\u001b[36;1m# First\\u001b[0m\\r\\n\\r\\n"},
  {time: 5, data: "\\u001b[36;1m# Second\\u001b[0m\\r\\n\\r\\n"},
  {time: 9, data: "\\u001b[36;1m# Third\\u001b[0m\\r\\n\\r\\n"},
]);
const points = guidedPausePoints.map((point) => ({
  time: point.time,
  heading: point.heading,
  debugOnly: Boolean(point.debugOnly),
}));
if (JSON.stringify(points) !== JSON.stringify([
  {time: 5, heading: 'First', debugOnly: false},
  {time: 9, heading: 'Second', debugOnly: true},
])) {
  console.error(JSON.stringify({points}));
  process.exit(1);
}
`, context);
""",
        parent_search="?debug=1",
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_arrow_keys_seek_by_ten_seconds() -> None:
    result = run_cast_player_script(
        r"""
vm.runInContext(`
totalSeconds = 120;
currentSeconds = 50;
playing = false;
let prevented = 0;
handleKeyboardSeek({
  key: 'ArrowLeft',
  target: progress,
  preventDefault() { prevented += 1; },
});
if (currentSeconds !== 40 || prevented !== 1) {
  console.error(JSON.stringify({currentSeconds, prevented}));
  process.exit(1);
}
handleKeyboardSeek({
  key: 'ArrowRight',
  target: progress,
  preventDefault() { prevented += 1; },
});
if (currentSeconds !== 50 || prevented !== 2) {
  console.error(JSON.stringify({currentSeconds, prevented}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_voiceover_playing_keeps_ready_label() -> None:
    result = run_cast_player_script(
        r"""
vm.runInContext(`
audioReady = true;
playing = true;
pausedAtGuidedPoint = false;
currentSeconds = 2;
startedAt = -2000;
audioPlaybackSegments = [
  {
    presentationStart: 0,
    presentationEnd: 10,
    audioStart: 0,
    audioEnd: 10,
  },
];
audio = {
  currentTime: 0,
  duration: 10,
  muted: false,
  pause() {},
  play() { return Promise.resolve(); },
};
startAudio();
`, context);

Promise.resolve().then(() => {
  const voice = element('voice');
  if (voice.textContent !== '' || voice.dataset.state !== 'playing' || voice.title !== 'voiceover playing') {
    console.error(JSON.stringify({
      textContent: voice.textContent,
      state: voice.dataset.state,
      title: voice.title,
    }));
    process.exit(1);
  }
});
""",
        search="?cast=/casts/demo.cast&audio=/audio/demo.mp3&title=Demo",
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_voiceover_waiting_does_not_show_playing_state() -> None:
    result = run_cast_player_script(
        r"""
vm.runInContext(`
audioReady = true;
playing = true;
pausedAtGuidedPoint = false;
currentSeconds = 2;
startedAt = -2000;
audioPlaybackSegments = [
  {
    presentationStart: 5,
    presentationEnd: 10,
    audioStart: 0,
    audioEnd: 5,
  },
];
audio = {
  currentTime: 0,
  duration: 10,
  muted: false,
  pause() {},
  play() { return Promise.resolve(); },
};
startAudio();
`, context);

Promise.resolve().then(() => {
  const voice = element('voice');
  if (voice.textContent !== '' || voice.dataset.state !== 'waiting' || voice.title !== 'voiceover waiting') {
    console.error(JSON.stringify({
      textContent: voice.textContent,
      state: voice.dataset.state,
      title: voice.title,
    }));
    process.exit(1);
  }
});
""",
        search="?cast=/casts/demo.cast&audio=/audio/demo.mp3&title=Demo",
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_audio_boundary_advances_to_next_segment_when_timer_fires_early() -> None:
    result = run_cast_player_script(
        r"""
vm.runInContext(`
now = 0;
performance.now = () => now;
audioReady = true;
playing = true;
pausedAtGuidedPoint = false;
currentSeconds = 0;
startedAt = 0;
audioPlaybackSegments = [
  {
    presentationStart: 0,
    presentationEnd: 10,
    audioStart: 0,
    audioEnd: 10,
  },
  {
    presentationStart: 10,
    presentationEnd: 20,
    audioStart: 10,
    audioEnd: 20,
  },
];
audio = {
  currentTime: 0,
  duration: 20,
  muted: false,
  pause() {},
  play() { return Promise.resolve(); },
};
startAudio();
`, context);

Promise.resolve().then(() => {
  const firstTimer = vm.runInContext('__timeoutCalls.at(-1)', context);
  if (!firstTimer || firstTimer.ms !== 10000) {
    console.error(JSON.stringify({firstTimer}));
    process.exit(1);
  }
  vm.runInContext(`
now = 9999;
__timeoutCalls.at(-1).callback();
`, context);
  Promise.resolve().then(() => {
    const state = vm.runInContext(`({
      currentSeconds,
      currentTime: audio.currentTime,
      voiceState: document.getElementById('voice').dataset.state,
      lastDelay: __timeoutCalls.at(-1).ms,
    })`, context);
    if (
      state.currentSeconds !== 10 ||
      state.currentTime !== 10 ||
      state.voiceState !== 'playing' ||
      state.lastDelay !== 10000
    ) {
      console.error(JSON.stringify(state));
      process.exit(1);
    }
  });
});
""",
        search="?cast=/casts/demo.cast&audio=/audio/demo.mp3&title=Demo",
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_arrow_keys_snap_to_hidden_stage_markers() -> None:
    result = run_cast_player_script(
        r"""
vm.runInContext(`
totalSeconds = 120;
audioPlaybackSegments = [
  {presentationStart: 0, presentationEnd: 20},
  {presentationStart: 61, presentationEnd: 80},
  {presentationStart: 100, presentationEnd: 120},
];
guidedPausePoints = [];
currentSeconds = 50;
playing = false;
let prevented = 0;
handleKeyboardSeek({
  key: 'ArrowRight',
  target: progress,
  preventDefault() { prevented += 1; },
});
if (currentSeconds !== 61 || prevented !== 1) {
  console.error(JSON.stringify({currentSeconds, prevented}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr


def test_cast_player_arrow_keys_ignore_text_inputs() -> None:
    result = run_cast_player_script(
        r"""
vm.runInContext(`
totalSeconds = 120;
currentSeconds = 50;
playing = false;
let prevented = 0;
const handled = handleKeyboardSeek({
  key: 'ArrowRight',
  target: {tagName: 'INPUT'},
  preventDefault() { prevented += 1; },
});
if (handled || currentSeconds !== 50 || prevented !== 0) {
  console.error(JSON.stringify({handled, currentSeconds, prevented}));
  process.exit(1);
}
`, context);
"""
    )

    assert result.returncode == 0, result.stderr
