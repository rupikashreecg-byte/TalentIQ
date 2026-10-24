// TalentIQ UI helpers and consent-based assessment/interview monitoring.
// Screen sharing is requested visibly through the browser. This prototype logs
// integrity events; it does NOT save or upload screen-recording video.
(function () {
  if (window.__talentIQMainJsLoaded) return;
  window.__talentIQMainJsLoaded = true;

  document.addEventListener('change', function (event) {
    if (event.target && event.target.matches('input[type="file"]')) {
      const label = event.target.closest('.file-picker');
      if (label && event.target.files.length) {
        const note = document.createElement('p');
        note.className = 'muted selected-file';
        note.textContent = 'Selected: ' + event.target.files[0].name;
        const old = label.querySelector('.selected-file');
        if (old) old.remove();
        label.appendChild(note);
      }
    }
  });

  document.addEventListener('DOMContentLoaded', function () {
    const panel = document.querySelector('[data-integrity-purpose]');
    if (!panel) return;

    const purpose = panel.dataset.integrityPurpose;
    const startButton = panel.querySelector('[data-integrity-start]');
    const status = panel.querySelector('[data-integrity-status]');
    // The answer form is outside the monitoring panel, so collect controls from the whole page.
    const protectedControls = document.querySelectorAll('[data-protected-control]');
    let stream = null;
    let started = false;
    let paused = false;
    let tabOutCount = 0;

    function setStatus(message, isError) {
      if (!status) return;
      status.textContent = message;
      status.classList.toggle('error-text', Boolean(isError));
    }

    function lockControls(locked) {
      document.querySelectorAll('[data-protected-control]').forEach(function (control) { control.disabled = locked; });
    }

    async function sendEvent(type, details) {
      try {
        const response = await fetch('/integrity/event', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ type: type, details: details || {} })
        });
        const data = await response.json();
        if (data.paused) pauseSession(data.reason || 'Session paused for review.');
        if (typeof data.tab_outs === 'number') tabOutCount = data.tab_outs;
        return data;
      } catch (error) {
        setStatus('Connection problem. Your answer controls are locked; reload only after checking with the supervisor.', true);
        lockControls(true);
        return null;
      }
    }

    function stopStream() {
      if (stream) stream.getTracks().forEach(function (track) { track.stop(); });
      stream = null;
    }

    function pauseSession(reason) {
      paused = true;
      lockControls(true);
      if (startButton) startButton.disabled = true;
      setStatus('SESSION PAUSED: ' + reason + '. Please contact the supervisor for review.', true);
      stopStream();
    }

    if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
      setStatus('This browser does not support screen sharing. Use a recent version of Chrome or Edge.', true);
      if (startButton) startButton.disabled = true;
      lockControls(true);
      return;
    }

    lockControls(true);
    setStatus('Fullscreen is required. Choose the TalentIQ exam/interview browser tab in the sharing picker. Window and entire-screen sharing are not accepted. This prototype logs events but does not save video.');

    if (startButton) startButton.addEventListener('click', async function () {
      if (started || paused) return;
      startButton.disabled = true;
      setStatus('Entering fullscreen and waiting for screen-sharing permission…');
      try {
        // Fullscreen must be requested from this direct button click because browsers
        // require a user gesture.
        // Trigger both permission-gated APIs directly from this click. Awaiting
        // fullscreen first can consume the transient user activation in some browsers.
        let fullscreenPromise = Promise.resolve();
        if (!document.fullscreenElement && document.documentElement.requestFullscreen) {
          fullscreenPromise = document.documentElement.requestFullscreen().catch(function (error) {
            return { error: error };
          });
        }
        // Prefer the current tab. Browser APIs cannot remove other picker choices,
        // so verify the selected surface and reject windows/entire-screen sharing.
        const sharingPromise = navigator.mediaDevices.getDisplayMedia({
          video: { displaySurface: 'browser' },
          audio: false,
          preferCurrentTab: true,
          selfBrowserSurface: 'include',
          surfaceSwitching: 'exclude'
        });
        stream = await sharingPromise;
        await fullscreenPromise;
        // Some Edge/Chrome builds dismiss a pending fullscreen request when the
        // share picker opens. Retry once after the candidate has selected the tab.
        if (!document.fullscreenElement && document.documentElement.requestFullscreen) {
          try { await document.documentElement.requestFullscreen(); } catch (fullscreenError) { /* fallback message below */ }
        }
        const videoTrack = stream.getVideoTracks().find(function (track) { return track.readyState === 'live'; });
        if (!videoTrack) {
          stopStream();
          throw new Error('No active browser-tab sharing track was provided.');
        }
        const settings = videoTrack.getSettings ? videoTrack.getSettings() : {};
        if (settings.displaySurface && settings.displaySurface !== 'browser') {
          stopStream();
          throw new Error('Please choose the TalentIQ browser tab only. Window and entire-screen sharing are not accepted. Click the button and try again.');
        }
        if (!settings.displaySurface) {
          stopStream();
          throw new Error('This browser cannot confirm that only a tab is shared. Please use an up-to-date Chrome or Edge browser and choose the TalentIQ tab.');
        }
        const response = await fetch('/integrity/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ purpose: purpose })
        });
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || 'Could not start monitored session.');
        started = true;
        lockControls(false);
        setStatus(document.fullscreenElement
          ? 'Fullscreen is active and the TalentIQ browser tab is being shared. Tab-outs are logged.'
          : 'The TalentIQ browser tab is being shared, but the browser blocked fullscreen. Press F11 to enter browser fullscreen, then keep this tab open.');

        stream.getVideoTracks()[0].addEventListener('ended', function () {
          if (!started || paused) return;
          sendEvent('screen_share_stopped').finally(function () {
            pauseSession('screen sharing stopped');
          });
        });

        document.addEventListener('visibilitychange', function () {
          if (!started || paused) return;
          if (document.hidden) {
            tabOutCount += 1;
            sendEvent('tab_out', { count_seen_by_client: tabOutCount });
            setStatus('Tab-out detected (' + tabOutCount + '). Keep this page visible. More than 3 pauses the session for review.', tabOutCount > 3);
          } else {
            sendEvent('page_visible');
          }
        });
        document.addEventListener('copy', function () {
          if (started && !paused) sendEvent('copy_attempt');
        });
        document.addEventListener('paste', function () {
          if (started && !paused) sendEvent('paste_attempt');
        });
        document.addEventListener('fullscreenchange', function () {
          if (started && !paused && !document.fullscreenElement) {
            sendEvent('fullscreen_exited');
          }
        });
      } catch (error) {
        stopStream();
        lockControls(true);
        startButton.disabled = false;
        setStatus('Monitoring did not start: ' + (error.message || 'screen-sharing permission was not granted') + ' You cannot submit until a browser tab is shared successfully.', true);
      }
    });

    function focusActiveQuestion() {
      const question = document.getElementById('active-question');
      if (question) {
        window.requestAnimationFrame(function () {
          question.scrollIntoView({ behavior: 'smooth', block: 'start' });
          question.focus({ preventScroll: true });
        });
      }
    }

    function updateMainContent(html, newPath) {
      const parsed = new DOMParser().parseFromString(html, 'text/html');
      const incoming = parsed.querySelector('.main-content');
      const current = document.querySelector('.main-content');
      if (!incoming || !current) throw new Error('Could not load the next session screen.');
      current.innerHTML = incoming.innerHTML;
      if (parsed.title) document.title = parsed.title;
      if (newPath) history.pushState({}, '', newPath);
      document.body.classList.add('exam-mode');
      focusActiveQuestion();
    }

    async function beginInterviewAfterBreak() {
      try {
        setStatus('Break complete. Preparing the interview…');
        const response = await fetch('/integrity/start', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ purpose: 'interview' })
        });
        const integrity = await response.json();
        if (!response.ok || !integrity.ok) throw new Error(integrity.error || 'Could not continue monitoring.');
        const pageResponse = await fetch('/interview?sequence=assessment');
        if (!pageResponse.ok) throw new Error('Could not load the interview page.');
        updateMainContent(await pageResponse.text(), '/interview?sequence=assessment');
        // Keep the same browser tab stream active across assessment, break and interview.
        const interviewPanel = document.querySelector('[data-integrity-purpose="interview"]');
        if (interviewPanel) {
          const button = interviewPanel.querySelector('[data-integrity-start]');
          const message = interviewPanel.querySelector('[data-integrity-status]');
          if (button) button.hidden = true;
          if (message) message.textContent = 'Screen sharing continued from the assessment. You can begin the interview now.';
        }
        lockControls(false);
        setStatus('Interview monitoring is active.');
      } catch (error) {
        setStatus('The interview could not start automatically: ' + error.message + ' Please contact the supervisor.', true);
      }
    }

    function startBreakCountdown() {
      const breakPanel = document.querySelector('[data-break-seconds]');
      if (!breakPanel || breakPanel.dataset.timerStarted === 'true') return;
      breakPanel.dataset.timerStarted = 'true';
      let remaining = Number(breakPanel.dataset.breakSeconds) || 210;
      const display = document.getElementById('break-countdown');
      const bar = document.getElementById('break-progress');
      const total = remaining;
      function paint() {
        const mins = Math.floor(remaining / 60).toString().padStart(2, '0');
        const secs = (remaining % 60).toString().padStart(2, '0');
        if (display) display.textContent = mins + ':' + secs;
        if (bar) bar.style.width = Math.max(0, remaining / total * 100) + '%';
      }
      paint();
      const interval = window.setInterval(function () {
        remaining -= 1;
        paint();
        if (remaining <= 0) {
          window.clearInterval(interval);
          beginInterviewAfterBreak();
        }
      }, 1000);
    }

    // AJAX submissions keep the same document alive, so fullscreen and the approved
    // browser-tab share can continue between questions and into the interview.
    document.addEventListener('submit', async function (event) {
      const form = event.target;
      if (!form.matches('form[data-question-form], form[data-interview-form]')) return;
      event.preventDefault();
      if (paused) return;
      const submitButton = form.querySelector('button[type="submit"]');
      if (submitButton) submitButton.disabled = true;
      try {
        const response = await fetch(form.action || window.location.href, {
          method: 'POST', body: new FormData(form),
          headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
        const html = await response.text();
        if (!response.ok) throw new Error('The response could not be submitted. Please contact the supervisor.');
        updateMainContent(html, window.location.pathname + window.location.search);
        if (document.querySelector('[data-break-seconds]')) {
          // The same browser tab and fullscreen document remain active during the break.
          startBreakCountdown();
        } else {
          lockControls(false);
          focusActiveQuestion();
        }
      } catch (error) {
        if (submitButton) submitButton.disabled = false;
        setStatus(error.message || 'Could not submit the answer.', true);
      }
    });

    focusActiveQuestion();
    startBreakCountdown();

    window.addEventListener('pagehide', function () {
      // The browser releases the capture when the tab actually closes. We intentionally
      // do not stop the server session during the assessment-to-interview AJAX flow.
      stopStream();
    });
  });
})();
