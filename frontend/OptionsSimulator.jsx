import React, { useState, useEffect } from 'react';
import {
  Play, Pause, ChevronLeft, ChevronRight, RefreshCw,
  Settings, Key, Layers, Activity, TrendingUp, ShieldAlert,
  Sliders, Info, Compass, HelpCircle, CheckCircle, Database
} from 'lucide-react';

export default function OptionsSimulator() {
  // Connection and API Key state
  const [apiKey, setApiKey] = useState('');
  const [secretKey, setSecretKey] = useState('');
  const [sessionToken, setSessionToken] = useState('');
  const [connected, setConnected] = useState(false);

  // Market setup and controls
  const [symbol, setSymbol] = useState('NIFTY');
  const [timeframe, setTimeframe] = useState('5m');
  const [activeTab, setActiveTab] = useState('terminal'); // 'terminal' | 'analytics'
  const [chartView, setChartView] = useState('payoff'); // 'payoff' | 'timelapse'

  // Dynamic simulation metadata and options chain loaded from API / Mock
  const [metadata, setMetadata] = useState({
    spot_price: 25000.0,
    day_open: 24880.0,
    futures_price: 25015.0,
    synthetic_futures: 25002.5,
    straddle_premium: 245.0,
    atm_iv: 16.5,
    pcr: 0.92,
    max_pain: 25000.0
  });

  const [activeExpiry, setActiveExpiry] = useState('11 AUG \'26');
  const [expiryList, setExpiryList] = useState([
    { label: '11 AUG \'26', dte: 4 },
    { label: '18 AUG \'26', dte: 11 },
    { label: '25 AUG \'26', dte: 18 },
    { label: '01 SEP \'26', dte: 25 }
  ]);

  const [chainRows, setChainRows] = useState([]);

  // Account state
  const [capital, setCapital] = useState(1000000);
  const [freeCash, setFreeCash] = useState(1000000);

  // Strategy Builder Draft Legs
  const [draftLegs, setDraftLegs] = useState([]);
  const [activePositions, setActivePositions] = useState([]);
  const [tradeLogs, setTradeLogs] = useState([]);

  // AutoPlay Controls
  const [isPlaying, setIsPlaying] = useState(false);
  const [playSpeed, setPlaySpeed] = useState(1); // x speed
  const [currentTimeIndex, setCurrentTimeIndex] = useState(0);
  const [timestamps, setTimestamps] = useState([
    "09:15:00", "09:20:00", "09:25:00", "09:30:00", "09:35:00", "09:40:00"
  ]);

  // Single-leg builder state
  const [manualSide, setManualSide] = useState('SELL');
  const [manualRight, setManualRight] = useState('CALL');
  const [manualStrike, setManualStrike] = useState(25000);
  const [manualQty, setManualQty] = useState(50);
  const [manualSL, setManualSL] = useState(20);

  // Load option chain on timestamp/symbol/expiry change
  useEffect(() => {
    // Generate simulated option chain based on StockMock specifications
    const S = symbol === 'BANKNIFTY' ? 52000 : (symbol === 'FINNIFTY' ? 23000 : 25000);
    const step = symbol === 'BANKNIFTY' ? 100 : 50;
    const atm = Math.round(S / step) * step;

    // Generate strikes: ATM ± 15 strikes
    const strikes = [];
    for (let i = -15; i <= 15; i++) {
        strikes.push(atm + i * step);
    }

    const rows = strikes.map(strike => {
        const dist = Math.abs(strike - S) / step;

        // Simulating Calls
        const c_intrinsic = Math.max(S - strike, 0);
        const c_time_val = Math.max(5, 150 * Math.exp(-dist * 0.15));
        const c_ltp = c_intrinsic + c_time_val;
        const c_oi = Math.round(Math.max(10, 50000 * Math.exp(-dist * 0.2)));
        const c_delta = Math.min(1.0, Math.max(0.0, 1 - normCdf((strike - S) / (S * 0.15))));

        // Simulating Puts
        const p_intrinsic = Math.max(strike - S, 0);
        const p_time_val = Math.max(5, 145 * Math.exp(-dist * 0.15));
        const p_ltp = p_intrinsic + p_time_val;
        const p_oi = Math.round(Math.max(10, 48000 * Math.exp(-dist * 0.2)));
        const p_delta = Math.min(0.0, Math.max(-1.0, -normCdf((S - strike) / (S * 0.15))));

        return {
            strike,
            is_atm: strike === atm,
            call: {
                ltp: Number(c_ltp.toFixed(2)),
                oi: c_oi,
                delta: Number(c_delta.toFixed(3)),
                iv: 16.5
            },
            put: {
                ltp: Number(p_ltp.toFixed(2)),
                oi: p_oi,
                delta: Number(p_delta.toFixed(3)),
                iv: 16.8
            }
        };
    });

    setChainRows(rows);

    // Update metadata
    setMetadata({
        spot_price: S,
        day_open: S - 120.0,
        futures_price: S + 15.0,
        synthetic_futures: S + 2.5,
        straddle_premium: 245.0,
        atm_iv: 16.5,
        pcr: 0.92,
        max_pain: atm
    });
  }, [symbol, activeExpiry, currentTimeIndex]);

  // Normal Distribution CDF helper for dynamic delta simulation
  function normCdf(x) {
    const b1 = 0.319381530;
    const b2 = -0.356563782;
    const b3 = 1.781477937;
    const b4 = -1.821255978;
    const b5 = 1.330274429;
    const p = 0.2316419;
    const c = 0.39894228;
    if (x >= 0.0) {
        const t = 1.0 / (1.0 + p * x);
        return (1.0 - c * Math.exp(-x * x / 2.0) * t *
        (t * (t * (t * (t * b5 + b4) + b3) + b2) + b1));
    } else {
        const t = 1.0 / (1.0 - p * x);
        return (c * Math.exp(-x * x / 2.0) * t *
        (t * (t * (t * (t * b5 + b4) + b3) + b2) + b1));
    }
  }

  // Handle direct trade dispatcher from Option Chain hover actions
  const handleTrade = (side, right, strike, price) => {
    const newLeg = {
      id: String(Date.now() + Math.random()),
      side,
      right,
      strike,
      qty: 50,
      premium: price,
      sl: 0,
      tp: 0
    };
    setDraftLegs(prev => [...prev, newLeg]);
  };

  const loadTemplate = (type) => {
    const S = metadata.spot_price;
    const step = symbol === 'BANKNIFTY' ? 100 : 50;
    const atm = Math.round(S / step) * step;

    let legs = [];
    if (type === 'straddle') {
      legs = [
        { id: '1', side: 'SELL', right: 'CALL', strike: atm, qty: 50, premium: 120, sl: 20 },
        { id: '2', side: 'SELL', right: 'PUT', strike: atm, qty: 50, premium: 115, sl: 20 }
      ];
    } else if (type === 'strangle') {
      legs = [
        { id: '1', side: 'SELL', right: 'CALL', strike: atm + step, qty: 50, premium: 65, sl: 25 },
        { id: '2', side: 'SELL', right: 'PUT', strike: atm - step, qty: 50, premium: 60, sl: 25 }
      ];
    } else if (type === 'condor') {
      legs = [
        { id: '1', side: 'BUY', right: 'PUT', strike: atm - 2 * step, qty: 50, premium: 20, sl: 0 },
        { id: '2', side: 'SELL', right: 'PUT', strike: atm - step, qty: 50, premium: 60, sl: 0 },
        { id: '3', side: 'SELL', right: 'CALL', strike: atm + step, qty: 50, premium: 65, sl: 0 },
        { id: '4', side: 'BUY', right: 'CALL', strike: atm + 2 * step, qty: 50, premium: 25, sl: 0 }
      ];
    }
    setDraftLegs(legs);
  };

  const deployStrategy = () => {
    if (draftLegs.length === 0) return;
    setActivePositions([...activePositions, ...draftLegs]);
    setDraftLegs([]);
  };

  const clearAll = () => {
    setActivePositions([]);
    setDraftLegs([]);
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col font-sans">
      {/* 1. TOP HEADER BRANDING */}
      <header className="border-b border-slate-800 bg-slate-900/50 backdrop-blur px-6 py-4 flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <TrendingUp className="text-emerald-500 h-8 w-8" />
          <div>
            <h1 className="text-xl font-bold tracking-tight">StockMock Option Chain Simulation Engine</h1>
            <p className="text-xs text-slate-400">High-Performance Black-Scholes Greeks, Real-time Playback, & Matrix Terminal</p>
          </div>
        </div>

        <div className="flex items-center space-x-6">
          <div className="bg-slate-800/80 rounded-lg px-4 py-2 border border-slate-700/50 flex space-x-6">
            <div>
              <span className="block text-[10px] text-slate-400 uppercase font-semibold">Total Margin</span>
              <span className="text-sm font-bold text-slate-100">₹{capital.toLocaleString()}</span>
            </div>
            <div>
              <span className="block text-[10px] text-slate-400 uppercase font-semibold">Free Cash</span>
              <span className="text-sm font-bold text-slate-300">₹{freeCash.toLocaleString()}</span>
            </div>
            <div>
              <span className="block text-[10px] text-slate-400 uppercase font-semibold">PCR</span>
              <span className="text-sm font-bold text-emerald-400">{metadata.pcr}</span>
            </div>
          </div>

          <button onClick={clearAll} className="p-2 bg-rose-950/40 hover:bg-rose-950/80 border border-rose-800/50 text-rose-300 rounded-lg text-xs font-bold transition flex items-center space-x-1">
            <RefreshCw className="h-3 w-3" />
            <span>Reset</span>
          </button>
        </div>
      </header>

      {/* 2. TOP METADATA BAR (STOCKMOCK SPECIFICATIONS) */}
      <section className="bg-slate-900/30 border-b border-slate-800/60 px-6 py-3.5 grid grid-cols-8 gap-4 text-center">
        <div className="bg-slate-950/30 border border-slate-800/50 rounded-lg p-2">
          <span className="block text-[10px] text-slate-400 uppercase font-bold tracking-wider mb-0.5">Day Open</span>
          <span className="text-sm font-extrabold text-slate-300">₹{metadata.day_open.toLocaleString()}</span>
        </div>
        <div className="bg-slate-950/30 border border-slate-800/50 rounded-lg p-2 ring-1 ring-emerald-500/20">
          <span className="block text-[10px] text-slate-400 uppercase font-bold tracking-wider mb-0.5">Spot Price</span>
          <span className="text-sm font-extrabold text-emerald-400">₹{metadata.spot_price.toLocaleString()}</span>
        </div>
        <div className="bg-slate-950/30 border border-slate-800/50 rounded-lg p-2">
          <span className="block text-[10px] text-slate-400 uppercase font-bold tracking-wider mb-0.5">Futures Price</span>
          <span className="text-sm font-extrabold text-slate-300">₹{metadata.futures_price.toLocaleString()}</span>
        </div>
        <div className="bg-slate-950/30 border border-slate-800/50 rounded-lg p-2">
          <span className="block text-[10px] text-slate-400 uppercase font-bold tracking-wider mb-0.5">Synthetic Futures</span>
          <span className="text-sm font-extrabold text-blue-400">₹{metadata.synthetic_futures.toLocaleString()}</span>
        </div>
        <div className="bg-slate-950/30 border border-slate-800/50 rounded-lg p-2">
          <span className="block text-[10px] text-slate-400 uppercase font-bold tracking-wider mb-0.5">Straddle Premium</span>
          <span className="text-sm font-extrabold text-purple-400">₹{metadata.straddle_premium}</span>
        </div>
        <div className="bg-slate-950/30 border border-slate-800/50 rounded-lg p-2">
          <span className="block text-[10px] text-slate-400 uppercase font-bold tracking-wider mb-0.5">ATM IV</span>
          <span className="text-sm font-extrabold text-amber-400">{metadata.atm_iv}%</span>
        </div>
        <div className="bg-slate-950/30 border border-slate-800/50 rounded-lg p-2">
          <span className="block text-[10px] text-slate-400 uppercase font-bold tracking-wider mb-0.5">PCR Indicator</span>
          <span className="text-sm font-extrabold text-slate-300">{metadata.pcr}</span>
        </div>
        <div className="bg-slate-950/30 border border-slate-800/50 rounded-lg p-2 ring-1 ring-rose-500/20">
          <span className="block text-[10px] text-slate-400 uppercase font-bold tracking-wider mb-0.5">Max Pain Strike</span>
          <span className="text-sm font-extrabold text-rose-400">₹{metadata.max_pain.toLocaleString()}</span>
        </div>
      </section>

      {/* 3. SIMULATOR WORKSPACE */}
      <div className="flex-1 flex overflow-hidden">
        {/* Sidebar Controls */}
        <aside className="w-80 border-r border-slate-800 bg-slate-900/10 p-5 flex flex-col space-y-6 overflow-y-auto">
          {/* Market Setup */}
          <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-4 space-y-4">
            <span className="text-xs font-bold uppercase tracking-wider text-slate-300 flex items-center space-x-1.5 border-b border-slate-800 pb-2">
              <Settings className="h-3.5 w-3.5 text-emerald-400" />
              <span>Asset Setup</span>
            </span>
            <div className="space-y-3">
              <div>
                <label className="block text-[10px] uppercase font-bold text-slate-400 mb-1">Underlying Asset</label>
                <select
                  value={symbol}
                  onChange={(e) => setSymbol(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-emerald-500 transition"
                >
                  <option value="NIFTY">NIFTY</option>
                  <option value="BANKNIFTY">BANKNIFTY (Tuesday Expiry)</option>
                  <option value="FINNIFTY">FINNIFTY</option>
                </select>
              </div>
            </div>
          </div>

          {/* Connection */}
          <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-4 space-y-4">
            <span className="text-xs font-bold uppercase tracking-wider text-slate-300 flex items-center space-x-1.5 border-b border-slate-800 pb-2">
              <Key className="h-3.5 w-3.5 text-blue-400" />
              <span>Breeze Client API</span>
            </span>
            <button
              onClick={() => setConnected(!connected)}
              className={`w-full py-2 ${connected ? 'bg-rose-900/60 text-rose-100 hover:bg-rose-900 border border-rose-800' : 'bg-emerald-600 hover:bg-emerald-500 text-slate-950'} font-bold text-xs rounded-lg transition`}
            >
              {connected ? 'Disconnect Breeze' : 'Connect ICICI Direct'}
            </button>
          </div>
        </aside>

        {/* Option Chain & Terminal Workspace */}
        <main className="flex-1 flex flex-col overflow-hidden bg-slate-950 p-6 space-y-6">
          {/* Time playback controls */}
          <div className="bg-slate-900/40 border border-slate-800/80 rounded-xl p-4 flex items-center justify-between">
            <div className="flex items-center space-x-4">
              <span className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center space-x-1">
                <Activity className="h-4 w-4 text-rose-500" />
                <span>Replay Player:</span>
              </span>
              <div className="flex bg-slate-950 border border-slate-800 rounded-lg p-0.5">
                <button
                  onClick={() => setCurrentTimeIndex(prev => Math.max(0, prev - 1))}
                  className="px-3 py-1.5 hover:bg-slate-900 rounded-md text-xs font-semibold flex items-center space-x-1 text-slate-300"
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                  <span>Prev Bar</span>
                </button>
                <div className="px-4 py-1.5 text-xs font-mono font-bold text-emerald-400 flex items-center">
                  {timestamps[currentTimeIndex]}
                </div>
                <button
                  onClick={() => setCurrentTimeIndex(prev => Math.min(timestamps.length - 1, prev + 1))}
                  className="px-3 py-1.5 hover:bg-slate-900 rounded-md text-xs font-semibold flex items-center space-x-1 text-slate-300"
                >
                  <span>Next Bar</span>
                  <ChevronRight className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>

            <div className="flex items-center space-x-4">
              <button
                onClick={() => setIsPlaying(!isPlaying)}
                className={`px-4 py-2 rounded-lg font-bold text-xs flex items-center space-x-2 transition ${isPlaying ? 'bg-rose-600 hover:bg-rose-500 text-slate-100' : 'bg-emerald-600 hover:bg-emerald-500 text-slate-950'}`}
              >
                {isPlaying ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
                <span>{isPlaying ? 'Pause AutoPlay' : 'Start AutoPlay'}</span>
              </button>
            </div>
          </div>

          {/* Expiry Selection Tabs */}
          <div className="flex items-center space-x-3 border-b border-slate-800/80 pb-2 overflow-x-auto">
            <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">Expiry List:</span>
            {expiryList.map((exp) => (
              <button
                key={exp.label}
                onClick={() => setActiveExpiry(exp.label)}
                className={`px-3 py-1.5 rounded-lg text-xs font-bold border transition ${activeExpiry === exp.label ? 'bg-emerald-950/30 text-emerald-400 border-emerald-500/50' : 'bg-slate-900/40 text-slate-400 border-slate-800/50 hover:text-slate-200'}`}
              >
                {exp.label} <span className="text-[10px] text-slate-500">[{exp.dte} DTE]</span>
              </button>
            ))}
          </div>

          {/* Tab Selection */}
          <div className="flex border-b border-slate-800 space-x-6">
            <button
              onClick={() => setActiveTab('terminal')}
              className={`pb-2 text-sm font-bold border-b-2 transition ${activeTab === 'terminal' ? 'border-emerald-500 text-emerald-400' : 'border-transparent text-slate-400'}`}
            >
              Option Chain & Strategy Builder
            </button>
            <button
              onClick={() => setActiveTab('analytics')}
              className={`pb-2 text-sm font-bold border-b-2 transition ${activeTab === 'analytics' ? 'border-emerald-500 text-emerald-400' : 'border-transparent text-slate-400'}`}
            >
              Active Positions & Analytics
            </button>
          </div>

          {/* Tab Workspaces */}
          <div className="flex-1 overflow-y-auto space-y-6">
            {activeTab === 'terminal' ? (
              <div className="space-y-6">
                {/* 3c. Interactive StockMock-style Option Chain Table */}
                <div className="bg-slate-900/30 border border-slate-800 rounded-xl overflow-hidden shadow-2xl">
                  {/* Grid Header Row */}
                  <div className="grid grid-cols-11 text-center font-bold text-slate-400 text-[10px] py-3.5 border-b border-slate-800/80 bg-slate-950/60 uppercase tracking-wider">
                    <div className="col-span-2">Call LTP (Delta)</div>
                    <div className="col-span-3">Call Open Interest</div>
                    <div className="bg-slate-900/50 py-1 text-slate-100 font-bold col-span-1 border-x border-slate-800">Strike</div>
                    <div className="col-span-3">Put Open Interest</div>
                    <div className="col-span-2">Put LTP (Delta)</div>
                  </div>

                  {/* Chain Rows */}
                  {chainRows.map((row) => (
                    <div
                      key={row.strike}
                      className={`grid grid-cols-11 text-center text-xs py-2.5 border-b border-slate-800/40 items-center transition relative group ${row.is_atm ? 'bg-emerald-950/15 border-y border-emerald-500/20' : 'hover:bg-slate-900/30'}`}
                    >
                      {/* Call Side LTP with hover triggers */}
                      <div className="col-span-2 font-mono flex items-center justify-center relative">
                        <span className={`${row.strike < metadata.spot_price ? 'text-amber-400 font-bold' : 'text-slate-200'}`}>
                          ₹{row.call.ltp} <span className="text-[10px] text-slate-500">({row.call.delta})</span>
                        </span>

                        {/* Hover buy/sell triggers */}
                        <div className="absolute inset-y-0 left-0 flex items-center space-x-1 opacity-0 group-hover:opacity-100 transition-opacity bg-slate-950 px-2 rounded-r">
                          <button onClick={() => handleTrade('BUY', 'CALL', row.strike, row.call.ltp)} className="bg-blue-600 hover:bg-blue-500 text-slate-100 px-2 py-0.5 rounded font-bold text-[10px]">B</button>
                          <button onClick={() => handleTrade('SELL', 'CALL', row.strike, row.call.ltp)} className="bg-rose-600 hover:bg-rose-500 text-slate-100 px-2 py-0.5 rounded font-bold text-[10px]">S</button>
                        </div>
                      </div>

                      {/* Call OI with visual volume bars */}
                      <div className="col-span-3 px-4 flex items-center justify-start space-x-3">
                        <div className="flex-1 bg-slate-800 h-2 rounded overflow-hidden">
                          <div className="bg-emerald-500 h-full" style={{ width: `${Math.min(100, (row.call.oi / 50000) * 100)}%` }} />
                        </div>
                        <span className="text-[10px] font-mono text-slate-400">{(row.call.oi / 1000).toFixed(1)}k</span>
                      </div>

                      {/* Highlighted ATM / Standard Strike prices */}
                      <div className={`col-span-1 py-1 font-bold border-x border-slate-800/50 ${row.is_atm ? 'text-emerald-400 bg-emerald-900/30 text-sm' : 'text-slate-300'}`}>
                        {row.strike.toLocaleString()}
                      </div>

                      {/* Put OI with visual volume bars */}
                      <div className="col-span-3 px-4 flex items-center justify-end space-x-3">
                        <span className="text-[10px] font-mono text-slate-400">{(row.put.oi / 1000).toFixed(1)}k</span>
                        <div className="flex-1 bg-slate-800 h-2 rounded overflow-hidden">
                          <div className="bg-rose-500 h-full" style={{ width: `${Math.min(100, (row.put.oi / 50000) * 100)}%` }} />
                        </div>
                      </div>

                      {/* Put Side LTP with hover triggers */}
                      <div className="col-span-2 font-mono flex items-center justify-center relative">
                        <span className={`${row.strike > metadata.spot_price ? 'text-purple-400 font-bold' : 'text-slate-200'}`}>
                          ₹{row.put.ltp} <span className="text-[10px] text-slate-500">({row.put.delta})</span>
                        </span>

                        {/* Hover buy/sell triggers */}
                        <div className="absolute inset-y-0 right-0 flex items-center space-x-1 opacity-0 group-hover:opacity-100 transition-opacity bg-slate-950 px-2 rounded-l">
                          <button onClick={() => handleTrade('BUY', 'PUT', row.strike, row.put.ltp)} className="bg-blue-600 hover:bg-blue-500 text-slate-100 px-2 py-0.5 rounded font-bold text-[10px]">B</button>
                          <button onClick={() => handleTrade('SELL', 'PUT', row.strike, row.put.ltp)} className="bg-rose-600 hover:bg-rose-500 text-slate-100 px-2 py-0.5 rounded font-bold text-[10px]">S</button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>

                {/* Strategy Builder legs and template loader */}
                <div className="bg-slate-900/30 border border-slate-800 rounded-xl p-5 space-y-6">
                  <div>
                    <h3 className="text-sm font-bold text-slate-200 mb-1 flex items-center space-x-1.5">
                      <Layers className="h-4 w-4 text-emerald-400" />
                      <span>Draft Multi-Leg Strategy</span>
                    </h3>
                  </div>

                  <div className="grid grid-cols-3 gap-4">
                    <button onClick={() => loadTemplate('straddle')} className="py-2 px-3 bg-slate-900 hover:bg-slate-850 border border-slate-800 text-xs font-bold rounded-lg transition text-slate-300">
                      Short Straddle
                    </button>
                    <button onClick={() => loadTemplate('strangle')} className="py-2 px-3 bg-slate-900 hover:bg-slate-850 border border-slate-800 text-xs font-bold rounded-lg transition text-slate-300">
                      Short Strangle
                    </button>
                    <button onClick={() => loadTemplate('condor')} className="py-2 px-3 bg-slate-900 hover:bg-slate-850 border border-slate-800 text-xs font-bold rounded-lg transition text-slate-300">
                      Iron Condor
                    </button>
                  </div>

                  {draftLegs.length > 0 && (
                    <div className="space-y-4">
                      <div className="bg-slate-950/60 rounded-xl border border-slate-800 overflow-hidden">
                        <table className="w-full text-left text-xs text-slate-300">
                          <thead className="bg-slate-900/50 text-slate-400 uppercase text-[10px] font-bold border-b border-slate-800">
                            <tr>
                              <th className="p-3">Side</th>
                              <th className="p-3">Type</th>
                              <th className="p-3">Strike</th>
                              <th className="p-3">Qty</th>
                              <th className="p-3">Premium</th>
                            </tr>
                          </thead>
                          <tbody>
                            {draftLegs.map((leg) => (
                              <tr key={leg.id} className="border-b border-slate-800/50">
                                <td className={`p-3 font-bold ${leg.side === 'BUY' ? 'text-emerald-400' : 'text-rose-400'}`}>{leg.side}</td>
                                <td className="p-3">{leg.right}</td>
                                <td className="p-3 font-mono">{leg.strike}</td>
                                <td className="p-3">{leg.qty}</td>
                                <td className="p-3">₹{leg.premium}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>

                      <div className="flex space-x-4">
                        <button onClick={() => setDraftLegs([])} className="flex-1 py-2.5 bg-slate-900 hover:bg-slate-850 border border-slate-800 text-slate-300 font-bold text-xs rounded-lg transition">
                          Clear Strategy
                        </button>
                        <button onClick={deployStrategy} className="flex-1 py-2.5 bg-emerald-600 hover:bg-emerald-500 text-slate-950 font-bold text-xs rounded-lg transition shadow-lg shadow-emerald-900/20">
                          🚀 Deploy Strategy Legs Together
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="space-y-6">
                {/* Active positions */}
                <div className="bg-slate-900/30 border border-slate-800 rounded-xl p-5 space-y-4">
                  <h3 className="text-sm font-bold text-slate-200">Active Options Positions</h3>
                  {activePositions.length === 0 ? (
                    <div className="text-center py-8 text-slate-500 text-xs">No active positions. Deploy a strategy or trade from option chain to see live positions.</div>
                  ) : (
                    <div className="bg-slate-950/60 rounded-xl border border-slate-800 overflow-hidden">
                      <table className="w-full text-left text-xs text-slate-300">
                        <thead className="bg-slate-900/50 text-slate-400 uppercase text-[10px] font-bold border-b border-slate-800">
                          <tr>
                            <th className="p-3">Side</th>
                            <th className="p-3">Type</th>
                            <th className="p-3">Strike</th>
                            <th className="p-3">Qty</th>
                            <th className="p-3">Entry Premium</th>
                            <th className="p-3">P&L</th>
                          </tr>
                        </thead>
                        <tbody>
                          {activePositions.map((pos) => (
                            <tr key={pos.id} className="border-b border-slate-800/50">
                              <td className={`p-3 font-bold ${pos.side === 'BUY' ? 'text-emerald-400' : 'text-rose-400'}`}>{pos.side}</td>
                              <td className="p-3">{pos.right}</td>
                              <td className="p-3 font-mono">{pos.strike}</td>
                              <td className="p-3">{pos.qty}</td>
                              <td className="p-3">₹{pos.premium}</td>
                              <td className="p-3 text-emerald-400 font-bold">+₹0.00</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
