# coding=utf-8

import sys

sys.path.append('..')
from backtest import *
from lib import dataapi
import pandas as pd
import matplotlib.pyplot as plt
import copy

# 显示所有列
pd.set_option('display.max_columns', None)
# 显示所有行
pd.set_option('display.max_rows', None)


def read_data(exchange, symbol, period, start_day, end_day):
    fname = '%s_%s_%s_%s_%s' % (exchange, symbol, period, start_day, end_day)
    fname = fname.replace('/', '_')
    fpath = mkfpath('.\\api_data', fname + '.csv')

    print('reading and writing %s...' % fname)

    # 如果没有文件
    if not os.path.exists(fpath):
        ohlc = dataapi.get_exsymbol_kline(exchange, symbol, period, start_day, end_day)[2]
        ohlc.reset_index(inplace=True)
        # 节省空间写入数据
        if not ohlc.empty:
            ohlc.to_csv(fpath)
    else:
        ohlc = pd.read_csv(fpath, index_col=[0])
    ohlc['date_time'] = pd.to_datetime(ohlc['date'])

    # 观察数据缺失情况
    count = ohlc['date_time'].diff().value_counts()
    if len(count) > 1:
        print('Warning: discontinuous data')
        print(count.head(), '\n')

    return ohlc


def calc_signal(df, param):
    df['ATR'] = talib.ATR(df['high'], df['low'], df['close'], param['ATR_P'])
    df['MA'] = talib.EMA(df['close'], param['MA_P'])
    df['ub'] = df['MA'] + df['ATR'] * param['ATR_N']
    df['lb'] = df['MA'] - df['ATR'] * param['ATR_N']

    # close 回归 lb 则做多
    df['open_long_signal'] = ((df['close'] > df['lb']).shift(1) & (df['close'] < df['lb']).shift(2)) * 1
    # close 上穿 MA 则平仓
    df['close_long_signal'] = ((df['close'] > df['MA']).shift(1)
                               & (df['close'] < df['MA']).shift(2)) * 1

    # close 回归 ub 则做空
    df['open_short_signal'] = ((df['close'] < df['ub']).shift(1)
                               & (df['close'] > df['ub']).shift(2)) * 1
    # close 下穿 MA 则平仓

    df['close_short_signal'] = ((df['close'] < df['MA']).shift(1)
                                & (df['close'] > df['MA']).shift(2)) * 1

    df['buy_price'] = df['open']
    df['sell_price'] = df['open']

    return df


def calc_alpha_signal(df, param, start_day, end_day, corr):
    if corr > 0:
        df["date_time"] = pd.to_datetime(df["date"])
        factor_df = df[(df['date_time'] >= start_day) & (df['date_time'] < end_day)]
        factor = factor_df[param]
        bin_alpha = pd.qcut(factor, q=10, retbins=True,duplicates="drop")[1]
        df["open_long_signal"] = (df[param].shift(1) >= bin_alpha[-2]*(1)) * 1
        df['close_long_signal'] = (df[param].shift(1) < bin_alpha[-2]*(1)) * 1
        df["open_short_signal"] = (df[param].shift(1) <= bin_alpha[1]*(1)) * 1
        df["close_short_signal"] = (df[param].shift(1) > bin_alpha[1]*(1)) * 1
        df['buy_price'] = df['open']
        df['sell_price'] = df['open']
    else:
        df["date_time"] = pd.to_datetime(df["date"])
        factor_df = df[(df['date_time'] >= start_day) & (df['date_time'] < end_day)]
        factor = factor_df[param]
        bin_alpha = pd.qcut(factor, q=10, retbins=True)[1]
        df["open_long_signal"] = (df[param].shift(1) <= bin_alpha[1]*(1)) * 1
        df['close_long_signal'] = (df[param].shift(1) > bin_alpha[1]*(1)) * 1
        df["open_short_signal"] = (df[param].shift(1) >= bin_alpha[-2]*(1)) * 1
        df["close_short_signal"] = (df[param].shift(1) < bin_alpha[-2]*(1)) * 1
        df['buy_price'] = df['open']
        df['sell_price'] = df['open']
    return df


def main_strategy(df, param, ini_capital):
    # initial capital: [tradecoin, basecoin]
    # 净值以basecoin计算
    capital = ini_capital
    fee = 0.00075
    slippage = 0.00025

    tradecoin_amt = []
    basecoin_amt = []
    signal = []
    stop_loss_buy=0
    stop_loss_sell=0

    for rid, row in df.iterrows():

        pos_tmp = (capital[0] * row.open) / (capital[0] * row.open + capital[1])
        # 空仓时，关注做多做空信号
        if (-0.2 < pos_tmp < 0.2):
            if row.open_long_signal and not row.close_long_signal:
                # 全仓买入 tradecoin
                b_price = row.buy_price * (1 + slippage)
                capital, delta = order_pct_to(1, capital, b_price, fee)
                signal.append([row.date_time, 'b0', b_price / (1 - fee), 1])
            elif row.open_short_signal and not row.close_short_signal:
                # 1倍杠杆做空 tradecoin
                s_price = row.sell_price * (1 - slippage)
                capital, delta = order_pct_to(-1, capital, s_price, fee)
                signal.append([row.date_time, 's1', s_price * (1 - fee), -1])
            else:
                signal.append([row.date_time,np.nan, np.nan, pos_tmp])
        # 满仓时，关注平仓信号
        elif pos_tmp >= 0.2 and row.close_long_signal:
            s_price = row.sell_price * (1 - slippage)
            capital, delta = order_pct_to(0, capital, s_price, fee)
            signal.append([row.date_time, 's0', s_price * (1 - fee), 0])
        elif pos_tmp <= -0.2 and row.close_short_signal:
            b_price = row.buy_price * (1 + slippage)
            capital, delta = order_pct_to(0, capital, b_price, fee)
            signal.append([row.date_time, 'b1', b_price / (1 - fee), 0])
        else:
            signal.append([row.date_time, np.nan, np.nan, pos_tmp])
        # 记录持币数量
        tradecoin_amt = np.append(tradecoin_amt, capital[0])
        basecoin_amt = np.append(basecoin_amt, capital[1])

    # 计算净值
    tradecoin_net = tradecoin_amt + basecoin_amt / df['close']
    basecoin_net = tradecoin_amt * df['close'] + basecoin_amt

    pos = tradecoin_amt / tradecoin_net

    end_capital = capital
    net_df = pd.DataFrame({'date_time': df['date_time'],
                           'close': df['close'],
                           'net': basecoin_net,
                           'pos': pos,
                           'tradecoin': tradecoin_amt,
                           'basecoin': basecoin_amt})

    signal_df = pd.DataFrame(signal, columns=['date_time', 'signal', 'price', 'pos'])

    df = df.merge(signal_df, how='outer', on='date_time')
    df = df.merge(net_df, how='outer', on='date_time')
    return net_df, signal_df, end_capital


def long_short_ret(net_df):
    net_df = net_df.copy()
    net_df['ret'] = net_df['net'] / net_df['net'].shift(1)
    long_ret = np.prod(net_df[net_df['pos'] > 0]['ret']) - 1
    short_ret = np.prod(net_df[net_df['pos'] < 0]['ret']) - 1
    return long_ret, short_ret


def month_profit(net_df):
    net_df = net_df.set_index('date_time')
    ret = []
    for gid, group in net_df.groupby(pd.Grouper(freq='M')):
        gid = group.index[0]
        long_ret, short_ret = long_short_ret(group)
        ret.append([gid, long_ret, short_ret])

    month_ret = pd.DataFrame(ret, columns=['date_time', 'long_return', 'short_return'])
    return month_ret


def summary_net(net_df, plot_in_loops):
    month_ret = month_profit(net_df)
    long_ret, short_ret = long_short_ret(net_df)
    long_hold, short_hold = mean_hold_k(net_df['pos'])
    pos = mean_position(net_df['pos'])
    long_times, short_times = trade_times(net_df['pos'])

    # 转换成日净值
    net_df.set_index('date_time', inplace=True)
    net_df = net_df.resample('1D').asfreq()
    net_df.reset_index(inplace=True)

    # 计算汇总
    net = net_df['net']
    date_time = net_df['date_time']
    base = net_df['close']
    tot_ret = total_ret(net)
    ann_ret = annual_ret(date_time, net)
    sharpe = sharpe_ratio(date_time, net)
    annualVolatility = AnnualVolatility(net)
    drawdown = max_drawdown(net)
    alpha, beta = alpha_beta(date_time, base, net)
    ir = infomation_ratio(date_time, base, net)
    ret_r = ann_ret / drawdown

    result = [tot_ret, ann_ret, sharpe, annualVolatility,
              drawdown, alpha, beta, ret_r, ir, long_hold, short_hold, pos, long_times, short_times,
              long_ret, short_ret,
              net_df['date_time'].iloc[0], net_df['date_time'].iloc[-1]]
    cols = ['tot_ret', 'ann_ret', 'sharpe', 'annualVolatility',
            'max_drawdown', 'alpha', 'beta', 'ret_ratio', 'ir', 'long_hold',
            "short_hold", 'position', 'long_times', "short_times", 'long_return',
            'short_return', 'start_time', 'end_time']

    if plot_in_loops:
        param_str = "conclusion_combine_two"

        net_df['close'] = net_df['close'] / net_df['close'].iloc[0]
        net_df['net'] = net_df['net'] / net_df['net'].iloc[0]

        fpath = mkfpath('api_figure_2', param_str + '.png')

        fig, ax = plt.subplots(2)
        net_df.plot(x='date_time', y=['close', 'net'],
                    title=param_str, grid=True, ax=ax[0])
        ax[0].set_xlabel('')
        month_ret['month'] = month_ret['date_time'].dt.strftime('%Y-%m')
        month_ret.plot(kind='bar', x='month',
                       y=['long_return', 'short_return'],
                       color=['r', 'b'], grid=True, ax=ax[1])
        plt.tight_layout()
        plt.savefig(fpath)

    return result, cols

def summary_signal(signal_df):
    signal_df=copy.deepcopy(signal_df.dropna())
    win_r, profit_r = win_profit_ratio(signal_df)
    result=[win_r,profit_r]
    cols=['win_r', 'profit_r']
    return result,cols

def do_backtest(df, param, start_day, end_day):
    ini_capital = [0, 1]
    print('backtesting %s %s %s...' % (param, start_day, end_day))

    # 回测时间段
    df["date_time"] = pd.to_datetime(df["date"])
    df = df[(df['date_time'] >= start_day) & (df['date_time'] < end_day)]

    net_df, signal_df, end_capital = main_strategy(df, param, ini_capital)
    return net_df, signal_df, end_capital


if __name__ == '__main__':
    exchange = 'BIAN'
    alphas = ["Alpha.alpha059_gtja","Alpha.alpha118_gtja"]
    params = ["Alpha.alpha059","Alpha.alpha118"]
    corr_list=[-1,1]
    start_day = pd.to_datetime('2017-08-18')
    end_day = pd.to_datetime('2018-10-01')

    start_day_hv = pd.to_datetime("2017-09-18")
    end_day_hv = pd.to_datetime("2018-03-01")

    start_day_lv = pd.to_datetime("2018-03-01")
    end_day_lv = pd.to_datetime("2018-10-01")

    stat_ls = []
    for i in range(len(alphas)):
        df = pd.read_csv('../factor_writedb/xbtusd_' + alphas[i] + '.csv', index_col=0)
        df = calc_alpha_signal(df, params[i], pd.to_datetime('2017-08-18'), pd.to_datetime('2018-10-01'), corr=corr_list[i])
        net_df, signal_df, end_capital = do_backtest(df, params[i], start_day, end_day)
        print(net_df["pos"])

        net_df_hv = net_df[(net_df['date_time'] >= start_day_hv) & (net_df['date_time'] < end_day_hv)]
        signal_df_hv = signal_df[(signal_df['date_time'] >= start_day_hv) & (signal_df['date_time'] < end_day_hv)]

        net_df_lv = net_df[(net_df['date_time'] >= start_day_lv) & (net_df['date_time'] < end_day_lv)]
        signal_df_lv = signal_df[(signal_df['date_time'] >= start_day_lv) & (signal_df['date_time'] < end_day_lv)]

        results, col_signal = summary_signal(signal_df)
        results_hv, col_signal_hv = summary_signal(signal_df_hv)
        results_lv, col_signal_lv = summary_signal(signal_df_lv)

        if i==0:
            net_df_all=net_df
            results_all=results

            net_df_all_hv = net_df_hv
            results_all_hv = results_hv

            net_df_all_lv = net_df_lv
            results_all_lv = results_lv
        else:
            net_df_all[["net","pos","tradecoin","basecoin"]]=net_df_all[["net","pos","tradecoin","basecoin"]]+net_df[["net","pos","tradecoin","basecoin"]]
            results_all=[(x+y)/2 for x,y in zip(results_all,results)]

            net_df_all_hv[["net", "pos", "tradecoin", "basecoin"]] = net_df_all_hv[["net", "pos", "tradecoin", "basecoin"]] + net_df_hv[["net", "pos", "tradecoin", "basecoin"]]
            results_all_hv = [(x + y) / 2 for x, y in zip(results_all_hv, results_hv)]

            net_df_all_lv[["net", "pos", "tradecoin", "basecoin"]] = net_df_all_lv[["net", "pos", "tradecoin", "basecoin"]] + net_df_lv[["net", "pos", "tradecoin", "basecoin"]]
            results_all_lv = [(x + y) / 2 for x, y in zip(results_all_lv, results_lv)]

    net_df_all[["date_time", "close"]] = net_df[["date_time", "close"]]
    #print(net_df_all)
    stat, cols = summary_net(net_df_all, 1)
    all_index = stat + results_all
    cols = cols + col_signal
    stat_ls.append(all_index)

    net_df_all_hv[["date_time", "close"]] = net_df_hv[["date_time", "close"]]
    stat, cols = summary_net(net_df_all_hv, 0)
    all_index = stat + results_all_hv
    cols = cols + col_signal_hv
    stat_ls.append(all_index)

    net_df_all_lv[["date_time", "close"]] = net_df_lv[["date_time", "close"]]
    stat, cols = summary_net(net_df_all_lv, 0)
    all_index = stat + results_all_lv
    cols = cols + col_signal_lv
    stat_ls.append(all_index)

    stat_df = pd.DataFrame(stat_ls, columns=cols)
    print(stat_df)

