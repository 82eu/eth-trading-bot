// 默认币种列表（首次加载时使用，之后从后端配置动态生成）
const DEFAULT_SYMBOLS = ["ETH-USDT-SWAP", "BTC-USDT-SWAP"];

// 可选周期列表
const ALL_TIMEFRAMES = ["5m", "15m", "30m", "1h", "4h"];

// 各币种的默认参数（新添加币种时使用，用户可自行修改）
const SYMBOL_DEFAULTS = {
    "ETH-USDT-SWAP": {
        name: "ETH",
        total_amount_usdt: 100,
        num_entries: 2,
        tp_points: 50,
        sl_points: 30,
        buffer_width: 10,
        enabled_tfs: ["5m", "15m"],
        step_amount: 1,
        step_tp: 1,
        step_sl: 1,
        step_buf: 1,
    },
    "BTC-USDT-SWAP": {
        name: "BTC",
        total_amount_usdt: 100,
        num_entries: 2,
        tp_points: 500,
        sl_points: 300,
        buffer_width: 100,
        enabled_tfs: ["5m", "15m"],
        step_amount: 1,
        step_tp: 10,
        step_sl: 10,
        step_buf: 10,
    },
};

// 新币种的通用默认值（添加新币种时使用）
const NEW_SYMBOL_DEFAULTS = {
    total_amount_usdt: 100,
    num_entries: 2,
    tp_points: 100,
    sl_points: 50,
    buffer_width: 20,
    enabled_tfs: ["5m", "15m"],
    step_amount: 1,
    step_tp: 1,
    step_sl: 1,
    step_buf: 1,
};

function symbolToFull(symbolShort) {
    return symbolShort + '-USDT-SWAP';
}

function symbolToShort(symbolFull) {
    return symbolFull.replace('-USDT-SWAP', '');
}

function getSymbolDefaults(symbol) {
    return SYMBOL_DEFAULTS[symbol] || { ...NEW_SYMBOL_DEFAULTS, name: symbolToShort(symbol) };
}

// 从后端配置中提取所有已配置的币种列表
function extractSymbolsFromConfig(data) {
    const symbols = new Set(DEFAULT_SYMBOLS);
    
    // 从 enabled_symbols 提取
    if (data.enabled_symbols && Array.isArray(data.enabled_symbols)) {
        data.enabled_symbols.forEach(s => {
            const full = s.includes('-') ? s.toUpperCase() : symbolToFull(s);
            symbols.add(full);
        });
    }
    
    // 从各 dict 参数中提取
    ['total_amount_usdt', 'num_entries', 'tp_points', 'sl_points', 'buffer_width'].forEach(key => {
        if (data[key] && typeof data[key] === 'object') {
            Object.keys(data[key]).forEach(s => symbols.add(s.toUpperCase()));
        }
    });
    
    return Array.from(symbols);
}

function createSymbolConfigPanel(symbol) {
    const defaults = getSymbolDefaults(symbol);
    const shortName = defaults.name || symbolToShort(symbol);
    
    const panel = document.createElement('div');
    panel.className = 'symbol-config-card';
    panel.dataset.symbol = symbol;
    
    // 生成周期复选框
    const tfCheckboxes = ALL_TIMEFRAMES.map(tf => {
        const checked = defaults.enabled_tfs.includes(tf) ? 'checked' : '';
        return `<label class="tf-check-mini"><input type="checkbox" class="tf-cb-mini" value="${tf}" ${checked}> ${tf}</label>`;
    }).join('');

    panel.innerHTML = `
        <div class="symbol-config-header">
            <span class="symbol-name">${shortName}</span>
            <div class="symbol-header-actions">
                <label class="symbol-enable-check">
                    <input type="checkbox" class="symbol-enable-cb" value="${shortName}" checked>
                    启用
                </label>
                <button class="symbol-remove-btn" title="删除此币种配置">×</button>
            </div>
        </div>
        <div class="symbol-config-body">
            <div class="config-row">
                <label>启用周期</label>
                <div class="tf-checks-mini">${tfCheckboxes}</div>
            </div>
            <div class="config-row">
                <label>总金额</label>
                <input type="number" class="cfg-input" data-key="total_amount_usdt"
                       value="${defaults.total_amount_usdt}" min="5" step="${defaults.step_amount}">
                <span class="unit">U</span>
            </div>
            <div class="config-row">
                <label>分批份数</label>
                <div class="entry-btns-mini">
                    <button class="entry-btn-mini ${defaults.num_entries === 1 ? 'active' : ''}" data-entries="1">1份</button>
                    <button class="entry-btn-mini ${defaults.num_entries === 2 ? 'active' : ''}" data-entries="2">2份</button>
                    <button class="entry-btn-mini ${defaults.num_entries === 3 ? 'active' : ''}" data-entries="3">3份</button>
                </div>
            </div>
            <div class="config-row">
                <label>止盈点数</label>
                <input type="number" class="cfg-input" data-key="tp_points"
                       value="${defaults.tp_points}" step="${defaults.step_tp}">
                <span class="unit">点</span>
            </div>
            <div class="config-row">
                <label>止损点数</label>
                <input type="number" class="cfg-input" data-key="sl_points"
                       value="${defaults.sl_points}" step="${defaults.step_sl}">
                <span class="unit">点</span>
            </div>
            <div class="config-row">
                <label>缓冲带</label>
                <input type="number" class="cfg-input" data-key="buffer_width"
                       value="${defaults.buffer_width}" step="${defaults.step_buf}">
                <span class="unit">点</span>
            </div>
        </div>
    `;
    
    // 分批份数按钮切换
    panel.querySelectorAll('.entry-btn-mini').forEach(btn => {
        btn.addEventListener('click', () => {
            panel.querySelectorAll('.entry-btn-mini').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
        });
    });
    
    // 删除币种按钮
    const removeBtn = panel.querySelector('.symbol-remove-btn');
    if (removeBtn) {
        removeBtn.addEventListener('click', () => {
            panel.remove();
        });
    }
    
    return panel;
}

function collectSymbolConfigs() {
    const configs = {};
    const enabledSymbols = [];
    
    document.querySelectorAll('.symbol-config-card').forEach(card => {
        const symbol = card.dataset.symbol;
        const enabled = card.querySelector('.symbol-enable-cb').checked;
        
        if (enabled) {
            enabledSymbols.push(symbolToShort(symbol));
        }
        
        const activeBtn = card.querySelector('.entry-btn-mini.active');
        const cfg = {
            num_entries: activeBtn ? parseInt(activeBtn.dataset.entries) : 2,
            enabled_tfs: Array.from(card.querySelectorAll('.tf-cb-mini:checked')).map(cb => cb.value),
        };

        card.querySelectorAll('.cfg-input').forEach(input => {
            const key = input.dataset.key;
            cfg[key] = parseFloat(input.value) || 0;
        });
        
        configs[symbol] = cfg;
    });
    
    return { configs, enabledSymbols };
}

function populateSymbolConfigs(data) {
    const container = document.getElementById('symbolConfigsContainer');
    if (!container) return;
    
    container.innerHTML = '';
    
    // 从配置中提取所有币种
    const symbols = extractSymbolsFromConfig(data);
    
    symbols.forEach(symbol => {
        const panel = createSymbolConfigPanel(symbol);
        container.appendChild(panel);
        
        const shortName = symbolToShort(symbol);
        
        // 设置启用状态
        if (data.enabled_symbols && Array.isArray(data.enabled_symbols)) {
            const enabled = data.enabled_symbols.some(s => 
                s.toUpperCase() === symbol || s.toUpperCase() === shortName
            );
            panel.querySelector('.symbol-enable-cb').checked = enabled;
        }
        
        // 填充各配置项的值
        if (data.total_amount_usdt) {
            const val = typeof data.total_amount_usdt === 'object' 
                ? (data.total_amount_usdt[symbol] || data.total_amount_usdt[shortName])
                : data.total_amount_usdt;
            if (val !== undefined) {
                panel.querySelector('[data-key="total_amount_usdt"]').value = val;
            }
        }
        
        if (data.num_entries) {
            const val = typeof data.num_entries === 'object'
                ? (data.num_entries[symbol] || data.num_entries[shortName])
                : data.num_entries;
            if (val !== undefined) {
                panel.querySelectorAll('.entry-btn-mini').forEach(b => {
                    b.classList.toggle('active', parseInt(b.dataset.entries) === val);
                });
            }
        }
        
        ['tp_points', 'sl_points', 'buffer_width'].forEach(key => {
            if (data[key]) {
                const val = typeof data[key] === 'object'
                    ? (data[key][symbol] || data[key][shortName])
                    : data[key];
                if (val !== undefined) {
                    panel.querySelector(`[data-key="${key}"]`).value = val;
                }
            }
        });

        // 填充启用周期
        if (data.enabled_tfs) {
            let tfs = typeof data.enabled_tfs === 'object'
                ? (data.enabled_tfs[symbol] || data.enabled_tfs[shortName])
                : data.enabled_tfs;
            if (tfs && Array.isArray(tfs)) {
                panel.querySelectorAll('.tf-cb-mini').forEach(cb => {
                    cb.checked = tfs.includes(cb.value);
                });
            }
        }
    });
}

function getSymbolConfigsForSave() {
    const { configs, enabledSymbols } = collectSymbolConfigs();

    const payload = {
        enabled_symbols: enabledSymbols,
        total_amount_usdt: {},
        num_entries: {},
        tp_points: {},
        sl_points: {},
        buffer_width: {},
        enabled_tfs: {},
    };

    Object.keys(configs).forEach(symbol => {
        const cfg = configs[symbol];
        payload.total_amount_usdt[symbol] = cfg.total_amount_usdt;
        payload.num_entries[symbol] = cfg.num_entries;
        payload.tp_points[symbol] = cfg.tp_points;
        payload.sl_points[symbol] = cfg.sl_points;
        payload.buffer_width[symbol] = cfg.buffer_width;
        payload.enabled_tfs[symbol] = cfg.enabled_tfs;
    });

    return payload;
}

// 添加新币种
function addNewSymbol(symbolInput) {
    const container = document.getElementById('symbolConfigsContainer');
    if (!container) return;
    
    let shortName = symbolInput.value.trim().toUpperCase();
    if (!shortName) {
        alert('请输入币种名称');
        return;
    }
    
    // 转为完整格式
    const fullSymbol = shortName.includes('-') ? shortName : shortName + '-USDT-SWAP';
    
    // 校验格式
    if (!/^[A-Z]+-USDT-SWAP$/.test(fullSymbol)) {
        alert('币种名称只能包含字母，例如: SOL、DOGE、PEPE');
        return;
    }
    
    // 检查是否已存在
    const existing = container.querySelector(`.symbol-config-card[data-symbol="${fullSymbol}"]`);
    if (existing) {
        alert(`${shortName} 已存在配置`);
        return;
    }
    
    // 创建新配置卡片
    const panel = createSymbolConfigPanel(fullSymbol);
    container.appendChild(panel);
    
    // 清空输入框
    symbolInput.value = '';
    
    // 滚动到新卡片
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function initSymbolConfigs() {
    populateSymbolConfigs({});
}

window.addEventListener('DOMContentLoaded', () => {
    initSymbolConfigs();
});

// 暴露到全局
window.symbolToFull = symbolToFull;
window.symbolToShort = symbolToShort;
window.getSymbolDefaults = getSymbolDefaults;
window.createSymbolConfigPanel = createSymbolConfigPanel;
window.collectSymbolConfigs = collectSymbolConfigs;
window.populateSymbolConfigs = populateSymbolConfigs;
window.getSymbolConfigsForSave = getSymbolConfigsForSave;
window.addNewSymbol = addNewSymbol;
window.initSymbolConfigs = initSymbolConfigs;
