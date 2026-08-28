/ startup.q -- tick cache with websocket pub/sub.
/ Minimal standalone take on jonathonmcmurray's ws.q .wsu namespace
/ (https://github.com/jonathonmcmurray/ws.q, MIT) -- the library itself
/ drags in the qutil package system, so the ~30 lines are inlined here.
/ NB: never leave a lone "/" on its own line in this file -- q reads it as
/ the start of a block comment and silently swallows everything after it.
/ Protocol (JSON over websocket, same port q already listens on):
/   client -> {"type":"sub","syms":["AAPL","BTC-USD"]}
/   server -> {"table":"trades","data":[{"time":"2026-...","sym":"AAPL","price":1.0,"size":2.0},...]}
/   client -> {"type":"bars","sym":"AAPL","interval":60}     (interval in seconds)
/   server -> {"table":"bars","sym":"AAPL","data":[{"barTime":...,"open":...,"high":...,"low":...,"close":...,"vwap":...,"volume":...,"tickCount":...},...]}
/   client -> {"type":"avwap","sym":"AAPL","anchor":"2026-08-28T14:30:00"}
/   server -> {"table":"avwap","sym":"AAPL","anchor":"...","data":[{"time":...,"avwap":...},...],"pv":...,"vol":...}
/             (pv/vol are the running sum(price*size) and sum(size), so a live
/              client can extend the series per tick: avwap=(pv+p*s)%(vol+s))
/ A second sub replaces the first; closing the socket unsubscribes.

/ .j.j serializes floats at display precision, which defaults to 7
/ significant digits -- enough to corrupt a vwap on the wire. Full doubles:
system"P 17";

/ schema matches kdb-store/_INIT_SCHEMA -- idempotent, same guard
if[not `trades in key `.; trades:([] time:`timestamp$(); sym:`symbol$(); price:`float$(); size:`float$())];

/ websocket handle -> subscribed syms
.wsu.subs:(`int$())!();

/ ticks per sym pushed as snapshot on subscribe
.wsu.snapN:500;

.wsu.send:{[h;d] (neg h) .j.j `table`data!(`trades;0!d)};

.wsu.sub:{[h;syms]
  syms:(),syms;
  .wsu.subs[h]:syms;
  / neg-take cycles rows when the table is shorter than N, so clamp to count
  snap:raze{[s]t:select from trades where sym=s;(neg .wsu.snapN&count t)#t}each syms;
  if[count snap; .wsu.send[h;snap]];
 };

/ OHLCV+VWAP bars from the tick cache; `time xasc first because ticks land
/ out of order and first/last on an unsorted table give wrong open/close
.wsu.bars:{[s;iv]
  0!select open:first price, high:max price, low:min price, close:last price,
    vwap:size wavg price, volume:sum size, tickCount:count i
    by barTime:iv xbar time from `time xasc select from trades where sym=s};

/ anchored VWAP: the cumulative vwap series for one sym from an anchor
/ time forward, straight off the tick cache; pv/vol let a client extend
/ the series live without re-requesting
.wsu.avwap:{[s;t0]
  t:`time xasc select from trades where sym=s, time>=t0;
  `data`pv`vol!(
    0!select time, avwap:(sums price*size)%sums size from t;
    sum t[`price]*t`size; sum t`size)};

.z.ws:{
  msg:@[.j.k;x;{()!()}];
  if["sub"~msg`type; .wsu.sub[.z.w;`$msg`syms]];
  if["bars"~msg`type;
    (neg .z.w) .j.j `table`sym`data!(`bars;msg`sym;.wsu.bars[`$msg`sym;`long$1e9*msg`interval])];
  if["avwap"~msg`type;
    (neg .z.w) .j.j (`table`sym`anchor!(`avwap;msg`sym;msg`anchor)),.wsu.avwap[`$msg`sym;"P"$msg`anchor]];
 };

.z.wc:{.wsu.subs::.wsu.subs _ x;};

.wsu.pub:{[d]
  {[d;h;syms] p:select from d where sym in syms; if[count p; .wsu.send[h;p]]}[d]'[key .wsu.subs; value .wsu.subs];
 };

/ kdb-store's write_ticks calls upd when it exists, plain insert otherwise
upd:{[t;d] t insert d; if[`trades=t; .wsu.pub[d]]; };
