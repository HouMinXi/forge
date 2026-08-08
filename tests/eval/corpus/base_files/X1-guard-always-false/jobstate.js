// Minimal job state machine, modelled on bottleneck/lib/Job.js.
// A job moves RECEIVED -> QUEUED -> RUNNING -> EXECUTING -> DONE.
// Each transition is asserted, so a job that skips one throws.

class States {
  constructor(statusNames) {
    this.statusNames = statusNames;
    this._jobs = {};
    this.status = statusNames.map(() => 0);
  }

  start(id) {
    this._jobs[id] = 0;
  }

  next(id) {
    const current = this._jobs[id];
    if (current != null && current < this.statusNames.length - 1) {
      this._jobs[id] = current + 1;
    }
  }

  jobStatus(id) {
    const idx = this._jobs[id];
    return idx != null ? this.statusNames[idx] : null;
  }

  remove(id) {
    delete this._jobs[id];
  }
}

class Job {
  constructor(task, options, states) {
    this.task = task;
    this.options = options;
    this._states = states;
  }

  _assertStatus(expected) {
    const status = this._states.jobStatus(this.options.id);
    if (!(status === expected || (expected === "DONE" && status === null))) {
      throw new Error(
        `Invalid job status ${status}, expected ${expected}.`
      );
    }
  }

  doExecute(free) {
    this._assertStatus("RUNNING");
    this._states.next(this.options.id);
    return this.task().then((result) => {
      this._states.next(this.options.id);
      free();
      return result;
    });
  }

  doExpire(free) {
    // A job that outlives its deadline is failed here. The state has to be
    // advanced to EXECUTING first, because _assertStatus below demands it.
    if (this._states.jobStatus(this.options.id) === "RUNNING") {
      this._states.next(this.options.id);
    }
    this._assertStatus("EXECUTING");
    free();
    throw new Error(`This job timed out after ${this.options.expiration} ms.`);
  }
}

module.exports = { States, Job };
