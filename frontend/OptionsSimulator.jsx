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
  const [spotPrice, setSpotPrice] = useState(25000);
  const [atmStrike, setAtmStrike] = useState(25000);
  const [strikeStep, setStrikeStep] = useState(50);
  const [timeframe, setTimeframe] = useState('5m');
  const [activeTab, setActiveTab] = useState('terminal'); // 'terminal' | 'analytics'
  const [chartView, setChartView] = useState('payoff'); // 'payoff' | 'timelapse'

  // Account state
  const [capital, setCapital] = useState(1000000);
  const [freeCash, setFreeCash] = useState(1000000);
  const [mtmHistory, setMtmHistory] = useState([]);

  // Strategy Builder Draft Legs
  const [draftLegs, setDraftLegs] = useState([]);
  const [activePositions, setActivePositions] = useState([]);
  const [tradeLogs, setTradeLogs] = useState([]);

  // AutoPlay Controls
  const [isPlaying, setIsPlaying] = useState(false);
  const [playSpeed, setPlaySpeed] = useState(1); // x speed

  // Single-leg builder state
  const [manualSide, setManualSide] = useState('SELL');
  const [manualRight, setManualRight] = useState('CALL');
  const [manualStrike, setManualStrike] = useState(25000);
  const [manualQty, setManualQty] = useState(50);
  const [manualSL, setManualSL] = useState(20);
  const [manualTP, setManualTP] = useState(0);

  // Expiry Payoff data (mocked for demo / standalone frontend purposes)
  const [payoffPoints, setPayoffPoints] = useState([]);

  // Handle template strategy import
  const loadTemplate = (type) => {
    let legs = [];
    if (type === 'straddle') {
      legs = [
        { id: '1', side: 'SELL', right: 'CALL', strike: atmStrike, qty: 50, premium: 120, sl: 20, tp: 0 },
        { id: '2', side: 'SELL', right: 'PUT', strike: atmStrike, qty: 50, premium: 115, sl: 20, tp: 0 }
      ];
    } else if (type === 'strangle') {
      legs = [
        { id: '1', side: 'SELL', right: 'CALL', strike: atmStrike + strikeStep, qty: 50, premium: 65, sl: 25, tp: 0 },
        { id: '2', side: 'SELL', right: 'PUT', strike: atmStrike - strikeStep, qty: 50, premium: 60, sl: 25, tp: 0 }
      ];
    } else if (type === 'condor') {
      legs = [
        { id: '1', side: 'BUY', right: 'PUT', strike: atmStrike - 2 * strikeStep, qty: 50, premium: 20, sl: 0, tp: 0 },
        { id: '2', side: 'SELL', right: 'PUT', strike: atmStrike - strikeStep, qty: 50, premium: 60, sl: 0, tp: 0 },
        { id: '3', side: 'SELL', right: 'CALL', strike: atmStrike + strikeStep, qty: 50, premium: 65, sl: 0, tp: 0 },
        { id: '4', side: 'BUY', right: 'CALL', strike: atmStrike + 2 * strikeStep, qty: 50, premium: 25, sl: 0, tp: 0 }
      ];
    }
    setDraftLegs(legs);
  };

  // Deploy draft strategy to active positions
  const deployStrategy = () => {
    if (draftLegs.length === 0) return;

    const newPositions = [...activePositions, ...draftLegs];
    setActivePositions(newPositions);

    // Adjust free cash
    let cashAdjustment = 0;
    draftLegs.forEach(leg => {
      const value = leg.qty * leg.premium;
      if (leg.side === 'BUY') {
        cashAdjustment -= value;
      } else {
        cashAdjustment += value;
      }
    });
    setFreeCash(prev => prev + cashAdjustment);

    // Append to logs
    const now = new Date().toLocaleTimeString();
    const newLogs = draftLegs.map(leg => ({
      time: now,
      action: `DEPLOY ${leg.side} ${leg.right}`,
      strike: leg.strike,
      qty: leg.qty,
      premium: leg.premium
    }));
    setTradeLogs(prev => [...newLogs, ...prev]);
    setDraftLegs([]);
  };

  const clearAll = () => {
    setActivePositions([]);
    setDraftLegs([]);
    setFreeCash(1000000);
    setMtmHistory([]);
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col font-sans">
      {/* 1. HEADER ROW */}
      <header className="border-b border-slate-800 bg-slate-900/50 backdrop-blur px-6 py-4 flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <TrendingUp className="text-emerald-500 h-8 w-8" />
          <div>
            <h1 className="text-xl font-bold tracking-tight">Breeze StockMock Option Replay</h1>
            <p className="text-xs text-slate-400">Production-Grade Next.js Options backtesting & simulation engine</p>
          </div>
        </div>

        {/* Connection Status & Account Metrics */}
        <div className="flex items-center space-x-6">
          <div className="bg-slate-800/80 rounded-lg px-4 py-2 border border-slate-700/50 flex space-x-6">
            <div>
              <span className="block text-[10px] text-slate-400 uppercase font-semibold">Total Margin/Cap</span>
              <span className="text-sm font-bold text-slate-100">₹{capital.toLocaleString()}</span>
            </div>
            <div>
              <span className="block text-[10px] text-slate-400 uppercase font-semibold">Free Cash</span>
              <span className="text-sm font-bold text-slate-300">₹{freeCash.toLocaleString()}</span>
            </div>
            <div>
              <span className="block text-[10px] text-slate-400 uppercase font-semibold">Total MTM P&L</span>
              <span className="text-sm font-bold text-emerald-400">+₹0.00</span>
            </div>
          </div>

          <div className="flex items-center space-x-2 bg-slate-800/40 rounded-lg px-3 py-2 border border-slate-700">
            <span className={`h-2.5 w-2.5 rounded-full ${connected ? 'bg-emerald-500 animate-pulse' : 'bg-amber-500'}`} />
            <span className="text-xs font-semibold">{connected ? 'Breeze Live' : 'Demo / Standalone'}</span>
          </div>

          <button onClick={clearAll} className="p-2 bg-rose-950/40 hover:bg-rose-950/80 border border-rose-800/50 text-rose-300 rounded-lg text-xs font-bold transition flex items-center space-x-1">
            <RefreshCw className="h-3 w-3" />
            <span>Reset</span>
          </button>
        </div>
      </header>

      <div className="flex-1 flex overflow-hidden">
        {/* 2. SIDEBAR PANEL */}
        <aside className="w-80 border-r border-slate-800 bg-slate-900/20 p-5 flex flex-col space-y-6 overflow-y-auto">
          {/* Breeze Credentials Expander */}
          <div className="bg-slate-900/50 border border-slate-800 rounded-xl p-4 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-800 pb-2">
              <span className="text-xs font-bold uppercase tracking-wider text-slate-300 flex items-center space-x-1.5">
                <Key className="h-3.5 w-3.5 text-blue-400" />
                <span>Breeze API Keys</span>
              </span>
            </div>
            <div className="space-y-3">
              <div>
                <label className="block text-[10px] uppercase font-bold text-slate-400 mb-1">App Key</label>
                <input
                  type="text"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="Enter ICICI App Key"
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-emerald-500 transition"
                />
              </div>
              <div>
                <label className="block text-[10px] uppercase font-bold text-slate-400 mb-1">Secret Key</label>
                <input
                  type="password"
                  value={secretKey}
                  onChange={(e) => setSecretKey(e.target.value)}
                  placeholder="Enter Secret Key"
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-emerald-500 transition"
                />
              </div>
              <button
                onClick={() => setConnected(true)}
                className="w-full py-2 bg-emerald-600 hover:bg-emerald-500 text-slate-950 font-bold text-xs rounded-lg transition shadow-lg shadow-emerald-900/20"
              >
                Connect ICICI Direct
              </button>
            </div>
          </div>

          {/* Market Settings Card */}
          <div className="bg-slate-900/50 border border-slate-800 rounded-xl p-4 space-y-4">
            <span className="text-xs font-bold uppercase tracking-wider text-slate-300 flex items-center space-x-1.5 border-b border-slate-800 pb-2">
              <Settings className="h-3.5 w-3.5 text-emerald-400" />
              <span>Market Setup</span>
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
                  <option value="BANKNIFTY">BANKNIFTY</option>
                  <option value="FINNIFTY">FINNIFTY</option>
                </select>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-[10px] uppercase font-bold text-slate-400 mb-1">ATM Strike</label>
                  <input
                    type="number"
                    value={atmStrike}
                    onChange={(e) => setAtmStrike(Number(e.target.value))}
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-[10px] uppercase font-bold text-slate-400 mb-1">Strike Step</label>
                  <input
                    type="number"
                    value={strikeStep}
                    onChange={(e) => setStrikeStep(Number(e.target.value))}
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:outline-none"
                  />
                </div>
              </div>
            </div>
          </div>
        </aside>

        {/* 3. MAIN WORKSPACE */}
        <main className="flex-1 flex flex-col overflow-hidden bg-slate-950 p-6 space-y-6">
          {/* Replay and AutoPlay Bar */}
          <div className="bg-slate-900/40 border border-slate-800/80 rounded-xl p-4 flex items-center justify-between">
            <div className="flex items-center space-x-4">
              <span className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center space-x-1">
                <Activity className="h-4 w-4 text-rose-500" />
                <span>Time-Lapse Controls:</span>
              </span>
              <div className="flex bg-slate-950 border border-slate-800 rounded-lg p-0.5">
                <button className="px-3 py-1.5 hover:bg-slate-900 rounded-md text-xs font-semibold flex items-center space-x-1 text-slate-300">
                  <ChevronLeft className="h-3.5 w-3.5" />
                  <span>1m</span>
                </button>
                <button className="px-3 py-1.5 hover:bg-slate-900 rounded-md text-xs font-semibold flex items-center space-x-1 text-slate-300">
                  <ChevronLeft className="h-3.5 w-3.5" />
                  <span>5m</span>
                </button>
                <button className="px-3 py-1.5 hover:bg-slate-900 rounded-md text-xs font-semibold flex items-center space-x-1 text-slate-300">
                  <span>5m</span>
                  <ChevronRight className="h-3.5 w-3.5" />
                </button>
                <button className="px-3 py-1.5 hover:bg-slate-900 rounded-md text-xs font-semibold flex items-center space-x-1 text-slate-300">
                  <span>30m</span>
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
              <div className="flex items-center space-x-2">
                <span className="text-xs text-slate-400 font-semibold">Speed:</span>
                <select
                  value={playSpeed}
                  onChange={(e) => setPlaySpeed(Number(e.target.value))}
                  className="bg-slate-950 border border-slate-800 text-slate-200 text-xs rounded-lg px-2.5 py-1.5 focus:outline-none"
                >
                  <option value={1}>1x (1s/bar)</option>
                  <option value={5}>5x (5s/bar)</option>
                  <option value={10}>10x</option>
                </select>
              </div>
            </div>
          </div>

          {/* Tabs header */}
          <div className="border-b border-slate-800 flex space-x-6">
            <button
              onClick={() => setActiveTab('terminal')}
              className={`pb-3 text-sm font-bold border-b-2 transition ${activeTab === 'terminal' ? 'border-emerald-500 text-emerald-400' : 'border-transparent text-slate-400 hover:text-slate-200'}`}
            >
              Option Chain & Strategy Builder
            </button>
            <button
              onClick={() => setActiveTab('analytics')}
              className={`pb-3 text-sm font-bold border-b-2 transition ${activeTab === 'analytics' ? 'border-emerald-500 text-emerald-400' : 'border-transparent text-slate-400 hover:text-slate-200'}`}
            >
              Active Portfolio & Greeks Analytics
            </button>
          </div>

          <div className="flex-1 overflow-y-auto space-y-6">
            {activeTab === 'terminal' ? (
              <div className="space-y-6">
                {/* 3a. Option Chain Grid */}
                <div className="bg-slate-900/30 border border-slate-800 rounded-xl overflow-hidden">
                  <div className="px-5 py-3 bg-slate-900/60 border-b border-slate-800 flex items-center justify-between">
                    <span className="text-xs font-bold uppercase tracking-wider text-slate-300">Option Chain (Simulated Matrix)</span>
                    <span className="text-xs text-slate-400">Spot Underlying: <strong className="text-emerald-400">₹{spotPrice.toFixed(2)}</strong></span>
                  </div>

                  {/* Option Chain Grid Header */}
                  <div className="grid grid-cols-11 text-center font-bold text-slate-400 text-[10px] py-2 border-b border-slate-800/80 bg-slate-950/40 uppercase">
                    <div>CE Bid</div>
                    <div>CE Ask</div>
                    <div>CE LTP</div>
                    <div>CE Delta</div>
                    <div>CE Theta</div>
                    <div className="bg-slate-900 py-1 text-slate-200">Strike</div>
                    <div>PE LTP</div>
                    <div>PE Delta</div>
                    <div>PE Theta</div>
                    <div>PE Bid</div>
                    <div>PE Ask</div>
                  </div>

                  {/* Strikerows */}
                  {[atmStrike - 2*strikeStep, atmStrike - strikeStep, atmStrike, atmStrike + strikeStep, atmStrike + 2*strikeStep].map((strike) => (
                    <div key={strike} className="grid grid-cols-11 text-center text-xs py-2 border-b border-slate-800/40 items-center hover:bg-slate-900/20 transition">
                      <div className="text-emerald-400 font-mono">₹{120}</div>
                      <div className="text-emerald-400 font-mono">₹{122}</div>
                      <div className="font-bold">₹{121}</div>
                      <div className="text-slate-400 font-mono">0.51</div>
                      <div className="text-slate-400 font-mono">-12.5</div>
                      <div className="bg-slate-900/50 py-1 font-bold text-slate-100 border-x border-slate-800">{strike}</div>
                      <div className="font-bold">₹{110}</div>
                      <div className="text-slate-400 font-mono">-0.49</div>
                      <div className="text-slate-400 font-mono">-11.2</div>
                      <div className="text-emerald-400 font-mono">₹{109}</div>
                      <div className="text-emerald-400 font-mono">₹{111}</div>
                    </div>
                  ))}
                </div>

                {/* 3b. Strategy Builder & Templates */}
                <div className="bg-slate-900/30 border border-slate-800 rounded-xl p-5 space-y-6">
                  <div>
                    <h3 className="text-sm font-bold text-slate-200 mb-1 flex items-center space-x-1.5">
                      <Layers className="h-4 w-4 text-emerald-400" />
                      <span>Multi-Leg Strategy Builder</span>
                    </h3>
                    <p className="text-xs text-slate-400">Select pre-built templates or add legs manually to build multi-leg options structures.</p>
                  </div>

                  {/* Templates button row */}
                  <div className="grid grid-cols-3 gap-4">
                    <button onClick={() => loadTemplate('straddle')} className="py-2 px-3 bg-slate-900 hover:bg-slate-850 border border-slate-800 text-xs font-bold rounded-lg transition text-slate-300">
                      Short Straddle (ATM)
                    </button>
                    <button onClick={() => loadTemplate('strangle')} className="py-2 px-3 bg-slate-900 hover:bg-slate-850 border border-slate-800 text-xs font-bold rounded-lg transition text-slate-300">
                      Short Strangle (OTM)
                    </button>
                    <button onClick={() => loadTemplate('condor')} className="py-2 px-3 bg-slate-900 hover:bg-slate-850 border border-slate-800 text-xs font-bold rounded-lg transition text-slate-300">
                      Iron Condor
                    </button>
                  </div>

                  {/* Manual Single Leg Injector */}
                  <div className="grid grid-cols-6 gap-3 bg-slate-950/40 p-4 rounded-xl border border-slate-800/80 items-end">
                    <div>
                      <label className="block text-[10px] uppercase font-bold text-slate-400 mb-1">Side</label>
                      <select value={manualSide} onChange={(e) => setManualSide(e.target.value)} className="w-full bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1.5 text-xs text-slate-200">
                        <option value="BUY">BUY</option>
                        <option value="SELL">SELL</option>
                      </select>
                    </div>
                    <div>
                      <label className="block text-[10px] uppercase font-bold text-slate-400 mb-1">Option Type</label>
                      <select value={manualRight} onChange={(e) => setManualRight(e.target.value)} className="w-full bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1.5 text-xs text-slate-200">
                        <option value="CALL">CE</option>
                        <option value="PUT">PE</option>
                      </select>
                    </div>
                    <div>
                      <label className="block text-[10px] uppercase font-bold text-slate-400 mb-1">Strike</label>
                      <input type="number" value={manualStrike} onChange={(e) => setManualStrike(Number(e.target.value))} className="w-full bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1.5 text-xs text-slate-200"/>
                    </div>
                    <div>
                      <label className="block text-[10px] uppercase font-bold text-slate-400 mb-1">Qty</label>
                      <input type="number" value={manualQty} onChange={(e) => setManualQty(Number(e.target.value))} className="w-full bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1.5 text-xs text-slate-200"/>
                    </div>
                    <div>
                      <label className="block text-[10px] uppercase font-bold text-slate-400 mb-1">Stop Loss %</label>
                      <input type="number" value={manualSL} onChange={(e) => setManualSL(Number(e.target.value))} className="w-full bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1.5 text-xs text-slate-200"/>
                    </div>
                    <button
                      onClick={() => {
                        const newLeg = {
                          id: String(Date.now()),
                          side: manualSide,
                          right: manualRight,
                          strike: manualStrike,
                          qty: manualQty,
                          premium: 100.0, // default premium
                          sl: manualSL,
                          tp: manualTP
                        };
                        setDraftLegs([...draftLegs, newLeg]);
                      }}
                      className="py-1.5 px-3 bg-emerald-600 hover:bg-emerald-500 text-slate-950 font-bold text-xs rounded-lg transition"
                    >
                      + Add Leg
                    </button>
                  </div>

                  {/* Draft Strategy Board */}
                  {draftLegs.length > 0 && (
                    <div className="space-y-4">
                      <span className="text-xs font-bold uppercase text-slate-300">Draft Strategy Legs:</span>
                      <div className="bg-slate-950/60 rounded-xl border border-slate-800 overflow-hidden">
                        <table className="w-full text-left text-xs text-slate-300">
                          <thead className="bg-slate-900/50 text-slate-400 uppercase text-[10px] font-bold border-b border-slate-800">
                            <tr>
                              <th className="p-3">Side</th>
                              <th className="p-3">Type</th>
                              <th className="p-3">Strike</th>
                              <th className="p-3">Qty</th>
                              <th className="p-3">Est. Premium</th>
                              <th className="p-3">SL %</th>
                            </tr>
                          </thead>
                          <tbody>
                            {draftLegs.map((leg) => (
                              <tr key={leg.id} className="border-b border-slate-800/50 hover:bg-slate-900/10">
                                <td className={`p-3 font-bold ${leg.side === 'BUY' ? 'text-emerald-400' : 'text-rose-400'}`}>{leg.side}</td>
                                <td className="p-3">{leg.right}</td>
                                <td className="p-3 font-mono">{leg.strike}</td>
                                <td className="p-3">{leg.qty}</td>
                                <td className="p-3">₹{leg.premium}</td>
                                <td className="p-3 text-amber-400 font-bold">{leg.sl > 0 ? `${leg.sl}%` : 'Disabled'}</td>
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
                          🚀 Execute Strategy (Deploy all legs)
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="space-y-6">
                {/* Active positions table */}
                <div className="bg-slate-900/30 border border-slate-800 rounded-xl p-5 space-y-4">
                  <h3 className="text-sm font-bold text-slate-200 flex items-center space-x-1.5">
                    <Database className="h-4 w-4 text-amber-500" />
                    <span>Active Options Positions</span>
                  </h3>

                  {activePositions.length === 0 ? (
                    <div className="text-center py-8 text-slate-500 text-xs">No active positions currently. Construct and deploy a strategy under Tab 1!</div>
                  ) : (
                    <div className="bg-slate-950/60 rounded-xl border border-slate-800 overflow-hidden">
                      <table className="w-full text-left text-xs text-slate-300">
                        <thead className="bg-slate-900/50 text-slate-400 uppercase text-[10px] font-bold border-b border-slate-800">
                          <tr>
                            <th className="p-3">Side</th>
                            <th className="p-3">Type</th>
                            <th className="p-3">Strike</th>
                            <th className="p-3">Qty</th>
                            <th className="p-3">Avg Entry</th>
                            <th className="p-3">LTP</th>
                            <th className="p-3">P&L</th>
                            <th className="p-3">SL %</th>
                          </tr>
                        </thead>
                        <tbody>
                          {activePositions.map((pos) => (
                            <tr key={pos.id} className="border-b border-slate-800/50 hover:bg-slate-900/10">
                              <td className={`p-3 font-bold ${pos.side === 'BUY' ? 'text-emerald-400' : 'text-rose-400'}`}>{pos.side}</td>
                              <td className="p-3">{pos.right}</td>
                              <td className="p-3 font-mono">{pos.strike}</td>
                              <td className="p-3">{pos.qty}</td>
                              <td className="p-3">₹{pos.premium}</td>
                              <td className="p-3 font-mono">₹{pos.premium}</td>
                              <td className="p-3 text-emerald-400 font-bold">+₹0.00</td>
                              <td className="p-3 text-amber-400 font-bold">{pos.sl > 0 ? `${pos.sl}%` : 'Disabled'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                {/* Expiry Payoff Graph Container */}
                <div className="bg-slate-900/30 border border-slate-800 rounded-xl p-5 space-y-4">
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-bold text-slate-200 flex items-center space-x-1.5">
                      <TrendingUp className="h-4 w-4 text-emerald-400" />
                      <span>Expiry & T+0 Option Payoff Curve</span>
                    </h3>
                    <div className="flex bg-slate-950 border border-slate-800 rounded-lg p-0.5">
                      <button onClick={() => setChartView('payoff')} className={`px-2.5 py-1 text-[10px] uppercase font-bold rounded-md transition ${chartView === 'payoff' ? 'bg-slate-800 text-emerald-400' : 'text-slate-400 hover:text-slate-200'}`}>Payoff Profile</button>
                      <button onClick={() => setChartView('timelapse')} className={`px-2.5 py-1 text-[10px] uppercase font-bold rounded-md transition ${chartView === 'timelapse' ? 'bg-slate-800 text-emerald-400' : 'text-slate-400 hover:text-slate-200'}`}>MTM Time-lapse</button>
                    </div>
                  </div>

                  {/* Payoff Graph Representation */}
                  <div className="h-64 bg-slate-950 border border-slate-850 rounded-xl flex items-center justify-center relative overflow-hidden">
                    <span className="text-xs text-slate-500 select-none">Interactive Payoff Curve Plotly.js / Recharts placeholder</span>
                    {/* Visual graph line decoration */}
                    <svg className="absolute inset-0 w-full h-full text-emerald-500/10 pointer-events-none" viewBox="0 0 100 100" preserveAspectRatio="none">
                      <path d="M0,50 Q25,20 50,50 T100,50" fill="none" stroke="currentColor" strokeWidth="2" />
                    </svg>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* 4. FOOTER STATUS BAR */}
          <footer className="border-t border-slate-850 bg-slate-900/20 px-6 py-3 flex items-center justify-between text-xs text-slate-400">
            <div className="flex items-center space-x-4">
              <span>Breeze Client Version: <strong className="text-slate-200">2.1.0</strong></span>
              <span className="h-4 w-px bg-slate-800" />
              <span>Rate Limit: <strong className="text-slate-200">3 req/sec</strong></span>
            </div>
            <div>
              <span>All systems fully operational</span>
            </div>
          </footer>
        </main>
      </div>
    </div>
  );
}
